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

import os
import shutil
import unittest
import tempfile
from multiprocessing.pool import Pool

import numpy as np
from numpy.testing import assert_allclose, assert_equal
from nose.plugins.attrib import attr
import xarray as xr
from schwimmbad import MultiPool, SerialPool, pool

from holopy.core.utils import (
    ensure_array, ensure_listlike, mkdir_p, choose_pool)
from holopy.core.math import (
    rotate_points, rotation_matrix, transform_cartesian_to_spherical,
    transform_spherical_to_cartesian, transform_cartesian_to_cylindrical,
    transform_cylindrical_to_cartesian, transform_cylindrical_to_spherical,
    transform_spherical_to_cylindrical, find_transformation_function,
    keep_in_same_coordinates)
from holopy.core.tests.common import assert_obj_close, get_example_data


TOLS = {'atol': 1e-14, 'rtol': 1e-14}

class TestCoordinateTransformations(unittest.TestCase):
    @attr("fast")
    def test_transform_cartesian_to_spherical_returns_correct_shape(self):
        np.random.seed(12)
        xyz = np.random.randn(3, 10)
        rtp = transform_cartesian_to_spherical(xyz)
        self.assertTrue(rtp.shape == xyz.shape)

    @attr("fast")
    def test_transform_cartesian_to_spherical(self):
        np.random.seed(12)
        xyz = np.random.randn(3, 10)
        rtp = transform_cartesian_to_spherical(xyz)
        r_is_close = np.allclose(
            rtp[0],
            np.sqrt(np.sum(xyz**2, axis=0)),
            **TOLS)
        theta_is_close = np.allclose(
            rtp[1],
            np.arccos(xyz[2] / np.linalg.norm(xyz, axis=0)),
            **TOLS)
        phi_is_close = np.allclose(
            rtp[2],
            np.arctan2(xyz[1], xyz[0]) % (2 * np.pi),
            **TOLS)
        self.assertTrue(r_is_close)
        self.assertTrue(theta_is_close)
        self.assertTrue(phi_is_close)

    @attr("fast")
    def test_transform_cartesian_to_spherical_returns_phi_on_0_2pi(self):
        np.random.seed(12)
        xyz = np.random.randn(3, 10)
        rtp = transform_cartesian_to_spherical(xyz)
        phi = rtp[2]
        self.assertTrue(np.all(phi > 0))

    @attr("fast")
    def test_transform_cartesian_to_spherical_at_origin(self):
        xyz_0 = np.zeros((3, 1))
        rtp = transform_cartesian_to_spherical(xyz_0)
        xyz_1 = transform_spherical_to_cartesian(rtp)
        self.assertTrue(np.allclose(xyz_0, xyz_1, **TOLS))

    @attr("fast")
    def test_transform_spherical_to_cartesian(self):
        # check that spherical_to_cartesian is the inverse of cartesian_to_sph
        np.random.seed(12)
        xyz_0 = np.random.randn(3, 10)
        rtp = transform_cartesian_to_spherical(xyz_0)
        xyz_1 = transform_spherical_to_cartesian(rtp)
        self.assertTrue(np.allclose(xyz_0, xyz_1, **TOLS))

    @attr("fast")
    def test_transform_cartesian_to_cylindrical_returns_correct_shape(self):
        np.random.seed(12)
        xyz = np.random.randn(3, 10)
        rpz = transform_cartesian_to_cylindrical(xyz)
        self.assertTrue(rpz.shape == xyz.shape)

    @attr("fast")
    def test_transform_cartesian_to_cylindrical(self):
        np.random.seed(12)
        xyz = np.random.randn(3, 10)
        rpz = transform_cartesian_to_cylindrical(xyz)
        r_is_close = np.allclose(
            rpz[0],
            np.sqrt(xyz[0]**2 + xyz[1]**2),
            **TOLS)
        phi_is_close = np.allclose(
            rpz[1], np.arctan2(xyz[1], xyz[0]) % (2 * np.pi),
            **TOLS)
        z_is_close = np.allclose(xyz[2], rpz[2])
        self.assertTrue(r_is_close)
        self.assertTrue(phi_is_close)
        self.assertTrue(z_is_close)

    @attr("fast")
    def test_transform_cartesian_to_cylindrical_returns_phi_on_0_2pi(self):
        np.random.seed(12)
        xyz = np.random.randn(3, 10)
        rpz = transform_cartesian_to_cylindrical(xyz)
        phi = rpz[1]
        self.assertTrue(np.all(phi > 0))

    @attr("fast")
    def test_transform_cylindrical_to_cartesian(self):
        # check cylindrical_to_cartesian is the inverse of cartesian_to_cyl
        np.random.seed(12)
        xyz_0 = np.random.randn(3, 10)
        rpz = transform_cartesian_to_cylindrical(xyz_0)
        xyz_1 = transform_cylindrical_to_cartesian(rpz)
        self.assertTrue(np.allclose(xyz_0, xyz_1, **TOLS))

    @attr("fast")
    def test_transform_cylindrical_to_spherical(self):
        # Uses the pre-existing cartesian to cylindrical & spherical functions
        np.random.seed(12)
        xyz = np.random.randn(3, 20)

        rho_phi_z = transform_cartesian_to_cylindrical(xyz)
        r_theta_phi_true = transform_cartesian_to_spherical(xyz)
        r_theta_phi_check = transform_cylindrical_to_spherical(rho_phi_z)
        is_ok = np.allclose(r_theta_phi_true, r_theta_phi_check, **TOLS)
        self.assertTrue(is_ok)

    @attr("fast")
    def test_transform_spherical_to_cylindrical(self):
        # Uses the pre-existing cartesian to cylindrical & spherical functions
        np.random.seed(12)
        xyz = np.random.randn(3, 20)

        r_theta_phi = transform_cartesian_to_spherical(xyz)
        rho_phi_z_true = transform_cartesian_to_cylindrical(xyz)
        rho_phi_z_check = transform_spherical_to_cylindrical(r_theta_phi)
        is_ok = np.allclose(rho_phi_z_true, rho_phi_z_check, **TOLS)
        self.assertTrue(is_ok)

    @attr("fast")
    def test_find_transformation_function_returns_helpful_error(self):
        # This test will have to be changed if someone implements
        # spherical bipolar coordinates.
        self.assertRaises(
            NotImplementedError,
            find_transformation_function,
            'cartesian', 'spherical_bipolar')

    @attr("fast")
    def test_find_transformation_function(self):
        desired = [
            ('cartesian', 'spherical', transform_cartesian_to_spherical),
            ('cartesian', 'cylindrical', transform_cartesian_to_cylindrical),
            ('spherical', 'cartesian', transform_spherical_to_cartesian),
            ('cylindrical', 'cartesian', transform_cylindrical_to_cartesian),
            ('spherical', 'cylindrical', transform_spherical_to_cylindrical),
            ('cylindrical', 'spherical', transform_cylindrical_to_spherical),
            ]
        for initial, final, correct_method in desired:
            self.assertTrue(
                find_transformation_function(initial, final) is correct_method)

    @attr("fast")
    def test_keep_in_same_coordinates(self):
        np.random.seed(12)
        xyz = np.random.randn(3, 10)
        the_same = keep_in_same_coordinates(xyz)
        self.assertTrue(np.allclose(xyz, the_same, **TOLS))

    @attr("fast")
    def test_find_transformation_function_when_same(self):
        np.random.seed(12)
        xyz = np.random.randn(3, 10)
        for which in ['cartesian', 'spherical', 'cylindrical']:
            method = find_transformation_function(which, which)
            self.assertTrue(np.allclose(xyz, method(xyz), **TOLS))

    @attr("fast")
    def test_coordinate_transformations_work_when_z_is_a_scalar(self):
        # This just tests that the transformations work, not that they
        # are in the shape (N, 3), as some of the calculations prefer
        # to leave z as a scalar if it starts as one (e.g. mielens)
        np.random.seed(12)
        x, y = np.random.randn(2, 10)
        z = np.random.randn()

        rho = np.sqrt(x**2 + y**2)
        phi = np.arctan2(y, x)

        versions_to_check = [
            ('cartesian', 'spherical', [x, y, z]),
            ('cartesian', 'cylindrical', [x, y, z]),
            ('cylindrical', 'cartesian', [rho, phi, z]),
            ('cylindrical', 'spherical', [rho, phi, z]),
            ]
        for *version_to_check, coords in versions_to_check:
            method = find_transformation_function(*version_to_check)
            try:
                result = method(coords)
            except:
                msg = '_to_'.join(version_to_check) + ' failed'
                self.assertTrue(False, msg=msg)
        pass


#Test math
@attr("fast")
def test_rotate_single_point():
    points = np.array([1.,1.,1.])
    assert_allclose(rotate_points(points, np.pi, np.pi, np.pi),
                    np.array([-1.,  1., -1.]), 1e-5)


@attr("fast")
def test_rotation_matrix_degrees():
    assert_allclose(rotation_matrix(180., 180., 180., radians = False),
                    rotation_matrix(np.pi, np.pi, np.pi))


#test utils
@attr('fast')
def test_ensure_array():
    assert_equal(ensure_array(1.0), np.array([1.0]))
    assert_equal(ensure_array([1.0]), np.array([1.0]))
    assert_equal(ensure_array(np.array([1.0])), np.array([1.0]))
    len(ensure_array(1.0))
    len(ensure_array(np.array(1.0)))
    len(ensure_array([1.0]))
    len(ensure_array(False))
    len(ensure_array(xr.DataArray([12],dims='a',coords={'a':['b']})))
    len(ensure_array(xr.DataArray([12],dims='a',coords={'a':['b']}).sel(a=['b'])))
    len(ensure_array(xr.DataArray(12)))


def test_choose_pool():
    class dummy():
        def map():
            return None
    assert not isinstance(choose_pool(None), (pool.BasePool, Pool))
    assert isinstance(choose_pool(2), MultiPool)
    assert isinstance(choose_pool('all'), MultiPool)
    assert isinstance(choose_pool('auto'), (pool.BasePool, Pool))
    assert not isinstance(choose_pool(dummy), (pool.BasePool, Pool))


@attr('fast')
def test_ensure_listlike():
    assert ensure_listlike(None) == []


@attr("fast")
def test_mkdir_p():
    tempdir = tempfile.mkdtemp()
    mkdir_p(os.path.join(tempdir, 'a', 'b'))
    mkdir_p(os.path.join(tempdir, 'a', 'b'))
    shutil.rmtree(tempdir)
