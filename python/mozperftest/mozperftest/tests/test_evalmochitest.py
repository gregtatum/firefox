import json
import shutil
from unittest import mock

import pytest

from mozperftest.environment import TEST
from mozperftest.tests.support import EXAMPLE_MOCHITEST_TEST, get_running_env
from mozperftest.utils import NoEvalDataError


def eval_running_env(**kw):
    return get_running_env(flavor="eval-mochitest", **kw)


@mock.patch("mozperftest.test.mochitest.ON_TRY", new=False)
@mock.patch("mozperftest.utils.ON_TRY", new=False)
def test_eval_mochitest_extracts_payload():
    mach_cmd, metadata, env = eval_running_env(
        tests=[str(EXAMPLE_MOCHITEST_TEST)],
    )

    mochitest_layer = env.layers[TEST].layers[0]
    log_processor = mock.MagicMock()
    log_processor.match = ['INFO evalDataPayload { "score": 5 } ']

    try:
        mochitest_layer._extract_payload_from_log(log_processor, metadata)
        assert mochitest_layer.payloads_from_log == ['{ "score": 5 }']
    finally:
        shutil.rmtree(mach_cmd._mach_context.state_dir)


@mock.patch("mozperftest.test.mochitest.ON_TRY", new=False)
@mock.patch("mozperftest.utils.ON_TRY", new=False)
def test_eval_mochitest_handles_payload_and_writes_file(tmp_path):
    mach_cmd, metadata, env = eval_running_env(
        tests=[str(EXAMPLE_MOCHITEST_TEST)],
        output=str(tmp_path),
    )

    mochitest_layer = env.layers[TEST].layers[0]
    mochitest_layer.payloads_from_log.append('{"score": 7}')

    try:
        mochitest_layer._handle_payloads(metadata, "test_eval.html")
    finally:
        shutil.rmtree(mach_cmd._mach_context.state_dir)

    out_file = tmp_path / "test_eval-eval-data.json"
    assert out_file.is_file()
    assert json.loads(out_file.read_text()) == {"score": 7}

    results = metadata.get_results()
    assert len(results) == 1
    assert results[0]["name"] == "test_eval.html"
    assert results[0]["framework"]["name"] == "mozperftest"


@mock.patch("mozperftest.test.mochitest.ON_TRY", new=False)
@mock.patch("mozperftest.utils.ON_TRY", new=False)
def test_eval_mochitest_raises_on_missing_payload(tmp_path):
    mach_cmd, metadata, env = eval_running_env(
        tests=[str(EXAMPLE_MOCHITEST_TEST)],
        output=str(tmp_path),
    )

    mochitest_layer = env.layers[TEST].layers[0]

    try:
        with pytest.raises(NoEvalDataError):
            mochitest_layer._handle_payloads(metadata, "test_eval.html")
    finally:
        shutil.rmtree(mach_cmd._mach_context.state_dir)


@mock.patch("mozperftest.test.mochitest.ON_TRY", new=False)
@mock.patch("mozperftest.utils.ON_TRY", new=False)
def test_eval_mochitest_raises_on_multiple_payloads(tmp_path):
    mach_cmd, metadata, env = eval_running_env(
        tests=[str(EXAMPLE_MOCHITEST_TEST)],
        output=str(tmp_path),
    )

    mochitest_layer = env.layers[TEST].layers[0]
    mochitest_layer.payloads_from_log.extend(['{"score": 1}', '{"score": 2}'])

    try:
        with pytest.raises(NotImplementedError):
            mochitest_layer._handle_payloads(metadata, "test_eval.html")
    finally:
        shutil.rmtree(mach_cmd._mach_context.state_dir)
