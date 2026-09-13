from yttv.cli import main


def test_main_without_args_exits_zero() -> None:
    assert main([]) == 0
