"""多实例隔离（A10）与进程重启恢复（A9）。"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from conftest import cfg, new_loop, observe, run
from fcc import Client


def _broken_loop(client: Client) -> str:
    """一个必定抛异常的 loop：MPC 缺少必需模型。"""
    data = cfg()
    data["controller"]["kind"] = "mpc"
    loop = client.create_loop(data)
    observe(client, loop, "temp", 10.0)
    return loop


def test_A10_one_loop_exception_does_not_affect_another(client):
    good = new_loop(client)
    bad = _broken_loop(client)

    observe(client, good, "temp", 10.0)
    with pytest.raises(ValueError):
        run(client, bad)

    res = run(client, good)
    assert res.errors[0]["error"] == pytest.approx(40.0)
    assert len(res.executed_actions) == 1
    assert client.store.get_loop(good)["enabled"] in (0, 1)


def test_A10_tick_isolates_failure(client):
    from fcc.scheduler import Scheduler  # noqa: F401  (确保模块可独立导入)

    good = new_loop(client)
    bad = _broken_loop(client)
    client.update_config(good, {"scheduler": {"mode": "fixed", "interval_seconds": 0.001}})
    client.update_config(bad, {"scheduler": {"mode": "fixed", "interval_seconds": 0.001}})
    client.start_loop(good)
    client.start_loop(bad)

    ran = client.engine.tick()
    assert good in ran
    assert bad not in ran
    assert client.store.cycle_count(good) == 1

    kinds = [e["kind"] for e in client.store.list_audit(bad, 100)]
    assert "loop_error" in kinds


def test_loops_do_not_share_state(client):
    a = new_loop(client)
    b = new_loop(client)
    observe(client, a, "temp", 10.0)
    observe(client, b, "temp", 100.0)
    ra = run(client, a)
    rb = run(client, b)
    assert ra.errors[0]["error"] == pytest.approx(40.0)
    assert rb.errors[0]["error"] == pytest.approx(-50.0)
    assert a != b


def test_A9_restart_keeps_config_history_and_state(tmp_path):
    path = str(tmp_path / "restart.db")
    c1 = Client(path)
    loop = new_loop(c1)
    observe(c1, loop, "temp", 10.0)
    run(c1, loop)
    c1.start_loop(loop)
    before_cycles = c1.store.cycle_count(loop)
    before_config = c1.store.get_loop_config(loop)[0].model_dump()
    c1.close()

    c2 = Client(path)
    cfg_after, enabled, _ = c2.store.get_loop_config(loop)
    assert cfg_after.model_dump() == before_config
    assert enabled is True
    assert c2.store.cycle_count(loop) == before_cycles
    assert len(c2.get_history(loop, 10)) == before_cycles
    hist = c2.store.observation_history(loop, "temp", 10)
    assert hist and hist[-1].value == 10.0
    c2.close()


def test_A9_new_process_sees_same_data(tmp_path):
    """真起一个子进程读写同一个 db，验证跨进程一致（不是同进程缓存）。"""
    path = str(tmp_path / "subproc.db")
    c1 = Client(path)
    loop = new_loop(c1)
    observe(c1, loop, "temp", 33.0)
    run(c1, loop)
    c1.close()

    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, r"{__import__('pathlib').Path(__file__).resolve().parents[1] / 'src'}")
        from fcc import Client
        c = Client(r"{path}")
        loops = c.list_loops()
        hist = c.get_history(r"{loop}", 5)
        print(loops[0]["id"], len(hist), hist[0]["observations"]["temp"]["value"])
        c.close()
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert loop in out.stdout
    assert "33.0" in out.stdout


def test_two_clients_same_db_are_consistent(tmp_path):
    path = str(tmp_path / "shared.db")
    c1 = Client(path)
    c2 = Client(path)
    loop = new_loop(c1)
    observe(c1, loop, "temp", 21.0)
    run(c1, loop)
    assert c2.store.cycle_count(loop) == 1
    c1.close()
    c2.close()
