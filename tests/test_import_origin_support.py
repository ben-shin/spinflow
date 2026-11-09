"""Origin gates accept the checkout or one fully pinned installed wheel."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest


def _fake_install(tmp_path, monkeypatch):
    import import_origin_support as origin
    monkeypatch.setattr(origin,"_authority",None)
    purelib=tmp_path/"site-packages"
    package=purelib/"nmrnext"
    package.mkdir(parents=True)
    rows=[]
    wheel=tmp_path/"spinflow.whl"
    with zipfile.ZipFile(wheel,"w") as archive:
        for index in range(31):
            member=f"nmrnext/file{index:02d}.py"
            raw=f"# pinned {index}\n".encode()
            archive.writestr(member,raw)
            path=package/f"file{index:02d}.py"
            path.write_bytes(raw)
            rows.append({"path":str(path),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
    pin=hashlib.sha256(wheel.read_bytes()).hexdigest()
    monkeypatch.setattr(origin,"REVIEWED_WHEEL_SHA256",pin)
    monkeypatch.setattr(origin,"REVIEWED_WHEEL_NAME",wheel.name)
    monkeypatch.setattr(origin.sysconfig,"get_path",lambda key:str(purelib))
    dist=SimpleNamespace(metadata={"Name":"spinflow"},locate_file=lambda name:purelib/name)
    monkeypatch.setattr(origin.importlib.metadata,"distribution",lambda name:dist)
    return origin,wheel,pin,rows,package


def test_default_checkout_and_source_shadow_refusal(tmp_path):
    import nmrnext
    from import_origin_support import assert_package_origin
    assert_package_origin(nmrnext.__file__,__file__)
    shadow=tmp_path/"site-packages"/"nmrnext"/"__init__.py"
    shadow.parent.mkdir(parents=True);shadow.write_text("")
    with pytest.raises(AssertionError): assert_package_origin(shadow,__file__)


def test_explicit_installed_requires_registered_complete_witness(tmp_path,monkeypatch):
    import import_origin_support as origin
    monkeypatch.setattr(origin,"_authority",None)
    with pytest.raises(AssertionError): origin.assert_package_origin(__file__,__file__,mode="installed")
    origin,wheel,pin,rows,package=_fake_install(tmp_path,monkeypatch)
    origin.register_installed_origin(wheel,pin,rows)
    origin.assert_package_origin(package/"file00.py",__file__)
    with pytest.raises(AssertionError): origin.assert_package_origin(__file__,__file__)
    with pytest.raises(AssertionError): origin.assert_package_origin(package/"file00.py",__file__,mode="unknown")


@pytest.mark.parametrize("defect",["wheel","pin","missing","wrong_dist","wrong_path"])
def test_installed_witness_refuses_wrong_authority(tmp_path,monkeypatch,defect):
    origin,wheel,pin,rows,package=_fake_install(tmp_path,monkeypatch)
    if defect=="wheel":
        wheel.write_bytes(wheel.read_bytes()+b"changed")
    elif defect=="pin":
        rows[0]["sha256"]="0"*64
    elif defect=="missing":
        rows.pop()
    elif defect=="wrong_dist":
        monkeypatch.setattr(origin.importlib.metadata,"distribution",
                            lambda name:SimpleNamespace(metadata={"Name":"other"},locate_file=lambda name:package))
    else:
        rows[0]["path"]=str(tmp_path/"elsewhere.py")
    with pytest.raises(AssertionError):
        origin.register_installed_origin(wheel,pin,rows)
