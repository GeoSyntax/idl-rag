from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_nginx_proxy_preserves_sse_streams() -> None:
    config = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "proxy_http_version 1.1;" in config
    assert "proxy_buffering off;" in config
    assert "proxy_cache off;" in config
    assert "proxy_read_timeout 3600s;" in config
    assert "proxy_send_timeout 3600s;" in config
    assert 'proxy_set_header Connection "";' in config
    assert "add_header X-Accel-Buffering no always;" in config
