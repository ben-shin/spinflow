"""Explicit bounded CLI mode must not alter default eager recipes."""
import json

import numpy as np
import pytest

from nmrnext.cli import main
from nmrnext.io import read_dataset, write_dataset
from nmrnext.model import Axis, Dataset


def test_cli_explicit_block_plan_and_run_preserve_native_result(tmp_path, capsys):
    source = Dataset(np.arange(8*3*4*5, dtype=np.float32).reshape(8,3,4,5),
                     [Axis('a',3),Axis('b',4),Axis('c',5)], ['a','b','c'])
    write_dataset(source, tmp_path/'input.nmrh5', format='native')
    recipe = tmp_path/'recipe.json'
    recipe.write_text(json.dumps({'schema_version':'1','input':'input.nmrh5','output':'output.nmrh5',
                                  'operations':[{'op':'fft','axis':'c'}]}))
    assert main(['plan',str(recipe),'--block-memory','64MiB']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['streaming'] is True
    assert plan['execution_mode'] == 'bounded_native_hdf5'
    assert main(['run',str(recipe),'--block-memory','64MiB']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['execution_mode'] == 'bounded_native_hdf5'
    assert read_dataset(tmp_path/'output.nmrh5').data.shape == source.data.shape


def test_cli_refuses_competing_eager_and_block_memory_flags(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(['plan', str(tmp_path/'recipe.json'), '--memory','1GiB','--block-memory','64MiB'])
    assert error.value.code == 2


def test_cli_block_override_has_the_existing_memory_override_precedence(tmp_path, capsys):
    source = Dataset(np.ones((2, 8)), [Axis('h',8)], ['h'])
    write_dataset(source, tmp_path/'input.nmrh5', format='native')
    recipe = tmp_path/'recipe.json'
    recipe.write_text(json.dumps({'schema_version':'1','input':'input.nmrh5','output':'output.nmrh5',
                                  'operations':[], 'memory_bytes':'invalid unless overridden'}))
    assert main(['plan',str(recipe),'--block-memory','64MiB']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['memory_bytes'] == 64*1024**2
