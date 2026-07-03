import pytest
from app.services.terminal import TerminalFilter

@pytest.fixture
def terminal_filter():
    return TerminalFilter()

def test_short_output(terminal_filter):
    out = "hello\nworld"
    filtered, debug = terminal_filter.filter_output(out)
    assert filtered == out
    assert debug is False

def test_pytest_failure(terminal_filter):
    out = """
============================= test session starts ==============================
platform linux -- Python 3.10.12, pytest-7.4.4
rootdir: /app
collecting ... collected 1 item

tests/test_something.py F                                                [100%]

=================================== FAILURES ===================================
________________________________ test_failure _________________________________

    def test_failure():
>       assert False
E       assert False

tests/test_something.py:3: AssertionError
=========================== short test summary info ============================
FAILED tests/test_something.py::test_failure - assert False
============================== 1 failed in 0.05s ===============================
""" + "\nSome filler\n" * 100

    filtered, debug = terminal_filter.filter_output(out)
    
    assert debug is True
    assert "assert False" in filtered
    assert "test session starts" not in filtered # Should be omitted
    assert "Some filler" not in filtered

def test_tsc_error(terminal_filter):
    out = "Info: starting compilation...\n" * 50 + """
src/index.ts:10:5 - error TS2322: Type 'number' is not assignable to type 'string'.

10     let x: string = 5;
       ~~~~~

""" + "Info: compiling other files...\n" * 50

    filtered, debug = terminal_filter.filter_output(out)
    
    assert "error TS2322" in filtered
    assert "let x: string = 5" in filtered
    assert "starting compilation..." not in filtered

def test_no_errors_long_output(terminal_filter):
    out = "\n".join([f"Line {i}" for i in range(100)])
    filtered, debug = terminal_filter.filter_output(out)
    
    assert debug is False
    assert "Line 0" not in filtered
    assert "Line 99" in filtered
    assert "Line 70" in filtered # Within last 30 lines
