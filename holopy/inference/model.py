# Copyright 2011-2016, Vinothan N. Manoharan, Thomas G. Dimiduk,
# Rebecca W. Perry, Jerome Fung, Ryan McGorty, Anna Wang, Solomon Barkley
#
# This file is part of HoloPy.
#
# HoloPy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# HoloPy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with HoloPy.  If not, see <http://www.gnu.org/licenses/>.

from copy import copy

import numpy as np
import xarray as xr

from holopy.core.metadata import dict_to_array, make_subset_data
from holopy.core.utils import ensure_array, ensure_listlike, ensure_scalar
from holopy.core.holopy_object import HoloPyObject
from holopy.scattering.errors import (MultisphereFailure, TmatrixFailure,
                                      InvalidScatterer, MissingParameter)
from holopy.scattering.interface import calc_holo
from holopy.scattering.theory import MieLens
from holopy.inference.prior import Prior, Uniform, ComplexPrior, generate_guess


OPTICS_KEYS = ['medium_index', 'illum_wavelen',
               'illum_polarization', 'noise_sd']


def make_xarray(dim_name, keys, values):
    '''
    Packs values into xarray with new dim and coords (keys)
    '''
    if isinstance(values[0], xr.DataArray):
        new_dim = xr.DataArray(keys, dims=[dim_name], name=dim_name)
        return xr.concat(values, dim=new_dim)
    else:
        return xr.DataArray(np.array(values), coords=[keys], dims=dim_name)


def make_complex(real, imag):
    if isinstance(real, Prior) or isinstance(imag, Prior):
        return ComplexPrior(real, imag)
    else:
        return complex(real, imag)


def read_map(map_entry, parameter_values):
    '''
    Reads a map to create an object

    Parameters
    ----------
    map_entry:
        map or subset of map created by model methods
    parameter_values: listlike
        values to replace map placeholders in final object
    '''
    if isinstance(map_entry, str) and map_entry[:11] == '_parameter_':
        return parameter_values[int(map_entry[11:])]
    elif isinstance(map_entry, list):
        if len(map_entry) == 2 and callable(map_entry[0]):
            func, args = map_entry
            return func(*[read_map(arg, parameter_values) for arg in args])
        else:
            return [read_map(item, parameter_values) for item in map_entry]
    else:
        return map_entry


def edit_map_indices(map_entry, indices):
    '''
    Adjusts a map to account for ties between parameters

    Parameters
    ----------
    map_entry:
        map or subset of map created by model methods
    indices: listlike
        indices of parameters to be tied
    '''
    if isinstance(map_entry, list):
        return [edit_map_indices(item, indices) for item in map_entry]
    elif isinstance(map_entry, str) and map_entry[:11] == '_parameter_':
        old_index = int(map_entry.split("_")[-1])
        if old_index in indices:
            new_index = indices[0]
        elif old_index < indices[0]:
            new_index = old_index
        else:
            shift = (np.array(indices) < old_index).sum() - 1
            new_index = old_index - shift
        return '_parameter_{}'.format(new_index)
    else:
        return map_entry


class Model(HoloPyObject):
    """Model probabilites of observing data

    Compute probabilities that observed data could be explained by a set of
    scatterer and observation parameters.
    """
    def __init__(self, scatterer, noise_sd=None, medium_index=None,
                 illum_wavelen=None, illum_polarization=None, theory='auto',
                 constraints=[]):
        dummy_parameters = {key: [0, 0, 0] for key in scatterer.parameters}
        self._dummy_scatterer = scatterer.from_parameters(dummy_parameters)
        self.theory = theory
        self.constraints = ensure_listlike(constraints)
        if not (np.isscalar(noise_sd) or isinstance(noise_sd, (Prior, dict))):
            noise_sd = ensure_array(noise_sd)
        optics = [medium_index, illum_wavelen, illum_polarization, noise_sd]
        optics_parameters = {key: val for key, val in zip(OPTICS_KEYS, optics)}
        self._parameters = []
        self._parameter_names = []
        self._maps = {'scatterer': self._convert_to_map(scatterer.parameters),
                      'optics': self._convert_to_map(optics_parameters)}

    def _convert_to_map(self, parameter, name=''):
        if isinstance(parameter, (list, tuple, np.ndarray)):
            mapped = self._iterate_mapping(name + '.', enumerate(parameter))
        elif isinstance(parameter, dict):
            mapped = self._map_dictionary(parameter, name)
        elif isinstance(parameter, xr.DataArray):
            mapped = self._map_xarray(parameter, name)
        elif isinstance(parameter, ComplexPrior):
            mapped = self._map_complex(parameter, name)
        elif isinstance(parameter, Prior):
            index = self._check_for_ties(parameter, name)
            mapped = '_parameter_{}'.format(index)
        else:
            mapped = parameter
        return mapped

    def _iterate_mapping(self, prefix, pairs):
        return [self._convert_to_map(parameter, prefix + str(suffix))
                for suffix, parameter in pairs]

    def _map_dictionary(self, parameter, name):
        prefix = name + "." if len(name) > 0 else ""
        values_map = self._iterate_mapping(prefix, parameter.items())
        iterator = zip(parameter.keys(), values_map)
        dict_args = [[key, val] for key, val in iterator if val is not None]
        return [dict, [dict_args]]

    def _map_xarray(self, parameter, name):
        dim_name = parameter.dims[0]
        coord_keys = parameter.coords[dim_name].values.tolist()
        if len(parameter.dims) == 1:
            values = parameter.values
        else:
            values = [parameter.loc[{dim_name: key}] for key in coord_keys]
        values_map = self._iterate_mapping(name + '.', zip(coord_keys, values))
        return [make_xarray, [dim_name, coord_keys, values_map]]

    def _map_complex(self, parameter, name):
        mapping = ((key, getattr(parameter, key)) for key in ['real', 'imag'])
        return [make_complex, self._iterate_mapping(name + '.', mapping)]

    def _check_for_ties(self, parameter, name):
        tied = False
        for index, existing in enumerate(self._parameters):
            # can't simply check parameter in self._parameters because
            # then two priors defined separately, but identically will
            # match whereas this way they are counted as separate objects.
            if existing is parameter:
                tied = True
                shared_name = self._parameter_names[index].split(':', 1)[-1]
                if shared_name not in self._parameter_names:
                    self._parameter_names[index] = shared_name
                break
        if not tied:
            index = len(self._parameters)
            self._parameters.append(parameter)
            if parameter.name is not None:
                name = parameter.name
            if name in self._parameter_names:
                name += '_0'
            while name in self._parameter_names:
                counter, reversename = name[::-1].split("_", 1)
                name = reversename[::-1] + "_" + str(int(counter[::-1]) + 1)
            self._parameter_names.append(name)
        return index

    def add_tie(self, parameters_to_tie, new_name=None):
        """
        Defines new ties between model parameters

        Parameters
        ----------
        parameters_to_tie: listlike
            names of parameters to tie, as given by keys in model.parameters
        new_name: string, optional
            the name for the new tied parameter
        """
        indices = []
        for par in parameters_to_tie:
            if par not in self._parameter_names:
                msg = ("Cannot tie parameter {}. It is not present in "
                       "parameters {}").format(par, self._parameter_names)
                raise ValueError(msg)
            first_value = self.parameters[parameters_to_tie[0]].renamed(None)
            if not self.parameters[par].renamed(None) == first_value:
                msg = "Cannot tie unequal parameters {} and {}".format(
                        par, parameters_to_tie[0])
                raise ValueError(msg)
            indices.append(self._parameter_names.index(par))
        indices.sort()
        for index in indices[:0:-1]:
            del(self._parameters[index])
            del(self._parameter_names[index])
        if new_name is not None:
            self._parameter_names[indices[0]] = new_name
        self._maps = {key: edit_map_indices(val, indices)
                      for key, val in self._maps.items()}

    def _iteritems(self):
        keys = ['scatterer', 'theory', '_parameters',
                '_parameter_names', '_maps']
        for key in keys:
            item = getattr(self, key)
            if isinstance(item, np.ndarray) and item.ndim == 1:
                item = list(item)
            yield key, item

    @classmethod
    def from_yaml(cls, loader, node):
        fields = loader.construct_mapping(node, deep=True)
        try:
            parameters = fields['_parameters']
            maps = fields['_maps']
        except KeyError:
            from holopy.fitting import fit_warning
            fit_warning('newly saved model object', 'the old one')
            kwargs = fields
        else:
            dummy_scatterer = fields['scatterer']
            scatterer_parameters = read_map(maps['scatterer'], parameters)
            scatterer = dummy_scatterer.from_parameters(scatterer_parameters)
            kwargs = {'scatterer': scatterer, 'theory': fields['theory']}
            kwargs.update(read_map(maps['optics'], parameters))
            if 'model' in maps:
                kwargs.update(read_map(maps['model'], parameters))
            if 'theory' in maps:
                kwargs.update(read_map(maps['theory'], parameters))
        return cls(**kwargs)

    @property
    def parameters(self):
        """
        dictionary of the model's parameters
        """
        return {name: par for name, par in zip(self._parameter_names,
                                               self._parameters)}

    @property
    def initial_guess(self):
        """
        list of initial guess value for each parameter
        """
        return [par.guess for par in self._parameters]

    @property
    def medium_index(self):
        return self._find_optics(self._parameters, None)['medium_index']

    @property
    def illum_wavelen(self):
        return self._find_optics(self._parameters, None)['illum_wavelen']

    @property
    def illum_polarization(self):
        return self._find_optics(self._parameters, None)['illum_polarization']

    @property
    def noise_sd(self):
        return self._find_noise(self._parameters, None)

    @property
    def scatterer(self):
        parameters = read_map(self._maps['scatterer'], self._parameters)
        return self._dummy_scatterer.from_parameters(parameters)

    def scatterer_from_parameters(self, pars):
        """
        Creates a scatterer by setting values for model parameters

        Parameters
        ----------
        pars: list

        Returns
        -------
        scatterer
        """
        pars = self.ensure_parameters_are_listlike(pars)
        scatterer_parameters = read_map(self._maps['scatterer'], pars)
        return self._dummy_scatterer.from_parameters(scatterer_parameters)

    def ensure_parameters_are_listlike(self, pars):
        if isinstance(pars, dict):
            from holopy.fitting import fit_warning
            fit_warning('list', 'a dictionary of parameters')
            pars = [pars[name] for name in self._parameter_names]
        return pars

    def _find_optics(self, pars, schema):
        """
        Creates optics dictionary by setting values for model parameters

        Parameters
        ----------
        pars: list
            values to create optics. Order should match model._parameters
        """
        mapped_optics = read_map(self._maps['optics'], pars)

        def find_parameter(key):
            if key in mapped_optics and mapped_optics[key] is not None:
                val = mapped_optics[key]
            elif hasattr(schema, key) and getattr(schema, key) is not None:
                val = getattr(schema, key)
            else:
                raise MissingParameter(key)
            return val
        return {key: find_parameter(key) for key in OPTICS_KEYS[:-1]}

    def _find_noise(self, pars, schema):
        """
        finds appropriate noise_sd for residuals calculations

        Parameters
        ----------
        pars: list
            values to create noise_sd. Order should match model._parameters
        """
        optics_map = read_map(self._maps['optics'], pars)
        if 'noise_sd' in optics_map and optics_map['noise_sd'] is not None:
            val = optics_map['noise_sd']
        elif hasattr(schema,'noise_sd'):
            val = schema.noise_sd
        else:
            raise MissingParameter(key)
        if val is None:
            if np.all([isinstance(par, Uniform) for par in self._parameters]):
                val = 1
            else:
                raise MissingParameter('noise_sd for non-uniform priors')
        return val

    def generate_guess(self, n=1, scaling=1, seed=None):
        return generate_guess(self._parameters, n, scaling, seed)

    def lnprior(self, par_vals):
        """
        Compute the log-prior probability of par_vals

        Parameters
        -----------
        par_vals: list
            values for each parameter in the order of self._parameters
        Returns
        -------
        lnprior: float
        """
        par_vals = self.ensure_parameters_are_listlike(par_vals)
        if 'scatterer' in self._maps:
            try:
                par_scat = self.scatterer_from_parameters(par_vals)
            except InvalidScatterer:
                return -np.inf

        for constraint in self.constraints:
            if not constraint.check(par_scat):
                return -np.inf
        return sum([p.lnprob(val) for p, val in
                    zip(self._parameters, par_vals)])

    def lnposterior(self, par_vals, data, pixels=None):
        """
        Compute the log-posterior probability of pars given data

        Parameters
        -----------
        pars: list
            values for each parameter in the order of self._parameters
        data: xarray
            The data to compute posterior against
        pixels: int(optional)
            Specify to use a random subset of all pixels in data

        Returns
        --------
        lnposterior: float
        """
        lnprior = self.lnprior(par_vals)
        # prior is sometimes used to forbid thing like negative radius
        # which will fail if you attempt to compute a hologram of, so
        # don't try to compute likelihood where the prior already
        # forbids you to be
        if lnprior == -np.inf:
            return lnprior
        else:
            if pixels is not None:
                data = make_subset_data(data, pixels=pixels)
            return lnprior + self.lnlike(par_vals, data)

    def forward(self, pars, detector):
        raise NotImplementedError("Implement in subclass")

    def _residuals(self, pars, data, noise):
        forward_model = self.forward(pars, data)
        return ((forward_model - data) / noise).values

    def lnlike(self, pars, data):
        """
        Compute the log-likelihood for pars given data

        Parameters
        -----------
        pars: list
            Values for each parameter in the order of self._parameters
        data: xarray
            The data to compute likelihood against

        Returns
        --------
        lnlike: float
        """
        pars = self.ensure_parameters_are_listlike(pars)
        noise_sd = self._find_noise(pars, data)
        N = data.size
        log_likelihood = ensure_scalar(
            -N/2 * np.log(2 * np.pi) -
            N * np.mean(np.log(ensure_array(noise_sd))) -
            0.5 * (self._residuals(pars, data, noise_sd)**2).sum())
        return log_likelihood

    def fit(self, data, strategy=None):
        from holopy.fitting import fit_warning
        from holopy.inference.interface import validate_strategy
        fit_warning('holopy.fit()', 'model.fit()')
        strategy = validate_strategy(strategy, 'fit')
        return strategy.fit(self, data)

    def sample(self, data, strategy=None):
        from holopy.fitting import fit_warning
        from holopy.inference.interface import validate_strategy
        fit_warning('holopy.sample()', 'model.sample()')
        strategy = validate_strategy(strategy, 'sample')
        return strategy.sample(self, data)

class LimitOverlaps(HoloPyObject):
    """
    Constraint prohibiting overlaps beyond a certain tolerance.
    fraction is the largest overlap allowed, in terms of sphere diameter.

    """
    def __init__(self, fraction=.1):
        self.fraction = fraction

    def check(self, s):
        return s.largest_overlap() <= ((np.min(s.r) * 2) * self.fraction)


class AlphaModel(Model):
    """
    Model of hologram image formation with scaling parameter alpha.
    """
    def __init__(self, scatterer, alpha=1, noise_sd=None, medium_index=None,
                 illum_wavelen=None, illum_polarization=None, theory='auto',
                 constraints=[]):
        super().__init__(scatterer, noise_sd, medium_index, illum_wavelen,
                         illum_polarization, theory, constraints)
        self._maps['model'] = self._convert_to_map({'alpha': alpha})

    @property
    def alpha(self):
        return read_map(self._maps['model'], self._parameters)['alpha']

    def forward(self, pars, detector):
        """
        Compute a hologram from pars with dimensions and metadata of detector,
        scaled by self.alpha.

        Parameters
        -----------
        pars: list
            Values for each parameter used to compute the hologram. Ordering
            is given by self._parameters
        detector: xarray
            dimensions of the resulting hologram. Metadata taken from
            detector if not given explicitly when instantiating self.
        """
        pars = self.ensure_parameters_are_listlike(pars)
        alpha = read_map(self._maps['model'], pars)['alpha']
        optics = self._find_optics(pars, detector)
        scatterer = self.scatterer_from_parameters(pars)
        try:
            return calc_holo(detector, scatterer, theory=self.theory,
                             scaling=alpha, **optics)
        except (MultisphereFailure, TmatrixFailure, InvalidScatterer):
            return -np.inf


# TODO: Change the default theory (when it is "auto") to be
# selected by the model.
# -- this is a little trickier than it sounds, because
# hlopy.scattering.determine_theory picks based off of whether the
# object is 1 sphere or a collection of spheres etc. So you can't
# pass MieLens as a theory
# For now it would be OK since PerfectLensModel only works with single
# spheres or superpositions, but let's leave this for later.
class ExactModel(Model):
    """
    Model of arbitrary scattering function given by calc_func.
    """
    def __init__(self, scatterer, calc_func=calc_holo, noise_sd=None,
                 medium_index=None, illum_wavelen=None,
                 illum_polarization=None, theory='auto', constraints=[]):
        super().__init__(scatterer, noise_sd, medium_index, illum_wavelen,
                         illum_polarization, theory, constraints)
        self.calc_func = calc_func

    def forward(self, pars, detector):
        """
        Compute a forward model (the hologram)

        Parameters
        -----------
        pars: list
            Values for each parameter used to compute the hologram. Ordering
            is given by self._parameters
        detector: xarray
            dimensions of the resulting hologram. Metadata taken from
            detector if not given explicitly when instantiating self.
        """
        pars = self.ensure_parameters_are_listlike(pars)
        optics = self._find_optics(pars, detector)
        scatterer = self.scatterer_from_parameters(pars)
        try:
            return self.calc_func(detector, scatterer, theory=self.theory, **optics)
        except (MultisphereFailure, InvalidScatterer):
            return -np.inf


class PerfectLensModel(Model):
    """
    Model of hologram image formation through a high-NA objective.
    """
    def __init__(self, scatterer, alpha=1.0, lens_angle=1.0, noise_sd=None,
                 medium_index=None, illum_wavelen=None, theory='auto',
                 illum_polarization=None, constraints=[]):
        super().__init__(scatterer, noise_sd, medium_index, illum_wavelen,
                         illum_polarization, theory, constraints)
        self._maps['model'] = self._convert_to_map({'alpha': alpha})
        self._maps['theory'] = self._convert_to_map({'lens_angle': lens_angle})

    @property
    def alpha(self):
        return read_map(self._maps['model'], self._parameters)['alpha']

    @property
    def lens_angle(self):
        return read_map(self._maps['theory'], self._parameters)['lens_angle']

    def forward(self, pars, detector):
        """
        Compute a forward model (the hologram)

        Parameters
        -----------
        pars: list
            Values for each parameter used to compute the hologram. Ordering
            is given by self._parameters
        detector: xarray
            dimensions of the resulting hologram. Metadata taken from
            detector if not given explicitly when instantiating self.
        """
        pars = self.ensure_parameters_are_listlike(pars)
        alpha = read_map(self._maps['model'], pars)['alpha']
        optics_kwargs = self._find_optics(pars, detector)
        scatterer = self.scatterer_from_parameters(pars)
        theory_kwargs = read_map(self._maps['theory'], pars)
        # FIXME would be nice to have access to the interpolator kwargs
        theory = MieLens(**theory_kwargs)
        try:
            return calc_holo(detector, scatterer, theory=theory,
                             scaling=alpha, **optics_kwargs)
        except InvalidScatterer:
            return -np.inf
