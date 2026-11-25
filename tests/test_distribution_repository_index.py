# SPDX-License-Identifier: GPL-3.0-or-later
"""Installer indices must follow Blender's enabled, valid repository sequence."""
import ast
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class RepositoryIndex(unittest.TestCase):
    def setUp(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'distribution.py').read_text(encoding='utf8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='own_runtime')
        namespace={'__package__':'bl_ext.owned.ac9_cloth_sim','RUNTIME_ID':'ac9_cloth_sim_runtime'}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'distribution.py','exec'),namespace)
        self.resolve=namespace['own_runtime']

    def run_resolver(self,repos):
        api=types.ModuleType('bl_pkg')
        api.repo_paths_or_none=lambda repo: ('valid' if repo.valid else None,'')
        context=types.SimpleNamespace(preferences=types.SimpleNamespace(extensions=types.SimpleNamespace(repos=repos)))
        with patch.dict(sys.modules,{'bl_pkg':api}):return self.resolve(context)

    def test_disabled_and_invalid_repositories_do_not_shift_installer_index(self):
        repo=lambda module,enabled=True,valid=True:types.SimpleNamespace(module=module,enabled=enabled,valid=valid)
        repos=[repo('disabled',False),repo('invalid',True,False),repo('other'),repo('owned')]
        module,index=self.run_resolver(repos)
        self.assertEqual(module,'bl_ext.owned.ac9_cloth_sim_runtime')
        self.assertEqual(index,1)

    def test_disabled_own_repository_is_unavailable(self):
        with self.assertRaisesRegex(RuntimeError,'unavailable'):
            self.run_resolver([types.SimpleNamespace(module='owned',enabled=False,valid=True)])

    def test_invalid_own_repository_is_unavailable(self):
        with self.assertRaisesRegex(RuntimeError,'unavailable'):
            self.run_resolver([types.SimpleNamespace(module='owned',enabled=True,valid=False)])
