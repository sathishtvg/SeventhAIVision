from worker import device


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("DEVICE", "cuda")
    assert device.resolve_device() == "cuda"
    monkeypatch.setenv("DEVICE", "cpu")
    assert device.resolve_device() == "cpu"


def test_cuda_used_when_available(monkeypatch):
    monkeypatch.delenv("DEVICE", raising=False)
    monkeypatch.setattr("torch.cuda.is_available", lambda: True)
    assert device.resolve_device() == "cuda"


def test_cpu_fallback_when_unavailable(monkeypatch):
    monkeypatch.delenv("DEVICE", raising=False)
    monkeypatch.setattr("torch.cuda.is_available", lambda: False)
    assert device.resolve_device() == "cpu"
