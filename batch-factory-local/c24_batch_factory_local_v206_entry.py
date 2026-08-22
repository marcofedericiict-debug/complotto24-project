from __future__ import annotations

import os

import c24_batch_factory_local_v206 as app


def self_test() -> None:
    current = app.core.APP_VERSION
    try:
        app.core.APP_VERSION = "2.0.5"
        app.v205._self_test_v205()
    finally:
        app.core.APP_VERSION = current

    class Dummy:
        def model_info(self, model):
            if "gpt-oss" in model:
                return {"capabilities": ["completion", "thinking"], "details": {"family": "gptoss"}}
            if "qwen3" in model:
                return {"capabilities": ["completion", "thinking"], "details": {"family": "qwen3"}}
            return {"capabilities": ["completion"], "details": {"family": "llama"}}

    d = Dummy()
    p1 = app._compat_profile(d, "gpt-oss:20b")
    p2 = app._compat_profile(d, "qwen3:8b")
    p3 = app._compat_profile(d, "llama3.2:3b")
    assert p1["think"] == "low" and p1["num_predict"] >= 6144
    assert p2["think"] is False
    assert p3["think"] is None
    assert len(app._build_variants(p1, True)) >= 3
    assert len(app._build_variants(p3, True)) >= 2
    assert app.core.APP_VERSION == "2.0.6"
    print("V2.0.6 ADAPTIVE LLM COMPATIBILITY SELF-TEST PASS")


def main() -> None:
    if "--self-test" in os.sys.argv:
        self_test()
        return
    app.core.ensure_dirs()
    if app.core.tk is None:
        raise SystemExit("Tkinter non disponibile")
    app.core.BatchFactoryGUI().run()


if __name__ == "__main__":
    main()
