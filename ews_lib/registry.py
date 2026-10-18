"""Model registry: versioned model bundles with metrics, features and a stage.

    data/models/<model_name>/v3/bundle.joblib
    data/models/<model_name>/v3/meta.json

Stages follow the usual lifecycle: "Champion" (the one the EWS uses),
"Challenger" (scored alongside for comparison) and "Archived". Registering a new
champion archives the previous one.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil

import joblib

from ews_lib import runtime
from ews_lib.config import MODELS_DIR, SERVING_DIR


def _mdir(name):
    return MODELS_DIR / name


def _versions(name):
    d = _mdir(name)
    if not d.exists():
        return []
    return sorted(int(p.name[1:]) for p in d.iterdir() if p.is_dir() and p.name.startswith("v") and (p / "meta.json").exists())


def _set_stage(name, version, stage):
    m = meta_of(name, version)
    m["stage"] = stage
    (_mdir(name) / f"v{version}" / "meta.json").write_text(json.dumps(m, indent=2))


def register(name: str, bundle: dict, *, stage="Challenger", family=None, approach=None, target=None, metrics=None,
             description=None, tags=None) -> int:
    """Register a new version. Earlier versions of the same model are archived; registering a Champion
    demotes the current champion of the same family (for example the PD model family) to Challenger."""
    versions = _versions(name)
    version = (versions[-1] + 1) if versions else 1
    d = _mdir(name) / f"v{version}"
    d.mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in bundle.items() if k != "p_oot"}  # predictions are not part of the artefact
    joblib.dump(keep, d / "bundle.joblib", compress=3)
    ctx = runtime.run_context()
    meta = dict(name=name, version=version, stage=stage, family=family or name, approach=approach, target=target,
                n_features=len(bundle.get("features", [])), features=list(bundle.get("features", [])),
                metrics=runtime.jsonable(metrics or {}), description=description, tags=tags or {},
                registered_at=dt.datetime.now().isoformat(timespec="seconds"), run_id=ctx["run_id"], task=ctx["task"],
                size_bytes=(d / "bundle.joblib").stat().st_size)
    for v in versions:
        if meta_of(name, v)["stage"] != "Archived":
            _set_stage(name, v, "Archived")
    if stage == "Champion":
        for m in list_models():
            if m["family"] == meta["family"] and m["name"] != name and m["stage"] == "Champion":
                _set_stage(m["name"], m["version"], "Challenger")
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    runtime.emit(dict(event="model_registered", name=name, version=version, stage=stage, approach=approach))
    if not runtime.in_kernel():
        print(f"  registered {name} v{version} ({stage})")
    return version


def meta_of(name, version):
    return json.loads((_mdir(name) / f"v{version}" / "meta.json").read_text())


def load(name: str, version: int | str = "Champion") -> dict:
    if version in ("Champion", "latest"):
        vs = _versions(name)
        if not vs:
            raise FileNotFoundError(f"No versions registered for {name}")
        if version == "Champion":
            champs = [v for v in vs if meta_of(name, v)["stage"] == "Champion"]
            version = champs[-1] if champs else vs[-1]
        else:
            version = vs[-1]
    bundle = joblib.load(_mdir(name) / f"v{version}" / "bundle.joblib")
    bundle["_registry"] = dict(name=name, version=int(version))
    return bundle


def champion_of(family: str) -> dict | None:
    champs = [m for m in list_models() if m.get("family") == family and m["stage"] == "Champion"]
    return sorted(champs, key=lambda m: m["registered_at"])[-1] if champs else None


def load_champion(family: str) -> dict:
    m = champion_of(family)
    if m is None:
        raise FileNotFoundError(f"No champion registered for {family}. Run the model training task first.")
    return load(m["name"], m["version"])


def list_models() -> list[dict]:
    out = []
    if not MODELS_DIR.exists():
        return out
    for d in sorted(p for p in MODELS_DIR.iterdir() if p.is_dir()):
        for v in _versions(d.name):
            out.append(meta_of(d.name, v))
    return out


def clear():
    if MODELS_DIR.exists():
        shutil.rmtree(MODELS_DIR)
    if SERVING_DIR.exists():
        shutil.rmtree(SERVING_DIR)


# ---------------------------------------------------------------------------
# Serving manifest: what the real-time service loads
# ---------------------------------------------------------------------------
def publish(manifest: dict):
    SERVING_DIR.mkdir(parents=True, exist_ok=True)
    manifest = dict(manifest, published_at=dt.datetime.now().isoformat(timespec="seconds"), **runtime.run_context())
    tmp = SERVING_DIR / ".manifest.json.tmp"
    tmp.write_text(json.dumps(runtime.jsonable(manifest), indent=2))
    tmp.replace(SERVING_DIR / "manifest.json")
    runtime.emit(dict(event="published", manifest=runtime.jsonable(manifest)))
    return manifest


def serving_manifest() -> dict | None:
    p = SERVING_DIR / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else None
