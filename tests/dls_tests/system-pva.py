import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(
    "/dls_sw/work/R3.14.12.3/support/pvaPy/lib/python/2.7/linux-x86_64")

from pkg_resources import require

require("numpy", "scanpointgenerator")

from malcolm.controllers.pva.pvautil import dict_to_pv_object
from malcolm.core import StringArray
from malcolm.compat import OrderedDict

import numpy as np

import unittest


# System tests for the pvaccess bits


class PVAUtilTest(unittest.TestCase):
    def setUp(self):
        self.d = OrderedDict([
            ('generator', OrderedDict([
                ('excluders', []),
                ('generators', [
                    {'typeid': 'scanpointgenerator:generator/LineGenerator:1.0',
                     'name': ['SampleY'],
                     'stop': [-3.0010000000000003],
                     'alternate_direction': False,
                     'start': [-3.099],
                     'num': 50,
                     'units': 'mm'},
                    {'typeid': 'scanpointgenerator:generator/LineGenerator:1.0',
                     'name': ['SampleX'],
                     'stop': [0.099],
                     'alternate_direction': False,
                     'start': [0.001],
                     'num': 50,
                     'units': 'mm'}]),
                ('mutators', [
                    {'duration': 0.049602,
                     'typeid': 'scanpointgenerator:mutator/FixedDurationMutator:1.0'}
                ])
            ])),
            ('empty', StringArray()),
            ('go', np.array([False, True])),
            ('stop', np.array([1, 2])),
            ('axesToMove', StringArray('SampleX', 'SampleY')),
            ('fileDir2', '/dls/i08/data/2017/cm16789-1/nexus/i08-4352'),
            ('fileDir', '/dls/i08/data/2017/cm16789-1/nexus/i08-4351')])

    def test_variant_union(self):
        d = dict_to_pv_object(self.d)
        assert d.getString("fileDir") == (
                          '/dls/i08/data/2017/cm16789-1/nexus/i08-4351')
        assert d.getString("fileDir2") == (
                          '/dls/i08/data/2017/cm16789-1/nexus/i08-4352')
        assert d.getScalarArray("axesToMove") == (
                          ['SampleX', 'SampleY'])
        # TODO: doesn't work for boolean arrays
        #self.assertEquals(d.getScalarArray("go"), [False, True])
        assert d.getScalarArray("stop") == [1, 2]
        assert d.getScalarArray("empty") == []


if __name__ == "__main__":
    unittest.main(verbosity=2)