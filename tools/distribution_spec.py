# SPDX-License-Identifier: GPL-3.0-or-later
"""One pin table shared by private builds, canonical ZIP splitting, and CI download checks."""
MAIN_ID = 'ac9_cloth_sim'
RUNTIME_ID = 'ac9_cloth_sim_runtime'
RUNTIME_VERSION = '1.0.0'
MODULE_VERSIONS = {'warp': '1.17.0', 'newton': '1.6.0'}
# Runtime release inputs belong to its own version, independent of main metadata.
RUNTIME_MAINTAINER = 'AC9 <cg-tech@omnisinc.co>'
RUNTIME_BLENDER_VERSION_MIN = '5.2.0'
PINS = {
    'newton-1.6.0-py3-none-any.whl': '5eedebe17d7261ca39e8f97c2f2f20e22e7264d0aed01782f3cb1dd60180b872',
    'warp_lang-1.17.0-py3-none-win_amd64.whl': 'ca35c82242a7553046f09023bbe56750b5c3d9af72625bd1a905a56d3189771d',
}
