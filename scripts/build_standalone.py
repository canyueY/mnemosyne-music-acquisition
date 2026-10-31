"""Build the Windows directory bundle; build tools are not end-user dependencies."""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="release/windows")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    if repo not in output.parents:
        raise ValueError("Build output must remain inside the project")
    resources = repo / "src/music_acquisition/resources"
    resources.mkdir(exist_ok=True)
    sys.path.insert(0, str(repo / "src"))
    import json
    from music_acquisition.integrations.mcp import TOOLS
    definitions = [{"name": name, "method": spec[0], "path": spec[1], "body": spec[2], "inputSchema": spec[3]} for name, spec in TOOLS.items()]
    for target in (resources / "business-tools.json", repo / "hosts/dsh-mnemosyne-music/business-tools.json"):
        target.write_text(json.dumps(definitions, ensure_ascii=False, indent=2), encoding="utf-8")
    for origin, name in [(repo / "monitor/index.html", "monitor.html"),
                         (repo / "domain/music_domain_pack.v1.json", "music_domain_pack.v1.json"),
                         (repo / "mnemosyne.component.json", "mnemosyne.component.json")]:
        shutil.copy2(origin, resources / name)
    with tempfile.TemporaryDirectory(prefix="music-acq-freeze-") as temporary:
        work = Path(temporary)
        entry = work / "launch.py"
        entry.write_text("from music_acquisition.cli import main\nif __name__ == '__main__':\n    raise SystemExit(main())\n", encoding="utf-8")
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--console",
            "--name", "mnemosyne-music", "--distpath", str(output), "--workpath", str(work / "build"),
            "--specpath", str(work), "--paths", str(repo / "src"),
            "--add-data", str(resources) + ";music_acquisition/resources",
            "--collect-submodules", "uvicorn", "--collect-submodules", "qrcode", "--collect-all", "tzdata",
            "--collect-submodules", "keyring.backends",
            "--hidden-import", "PIL.Image", "--hidden-import", "cryptography.fernet",
            "--exclude-module", "Murasame", "--exclude-module", "pytest", "--exclude-module", "numpy",
            str(entry)], cwd=repo, check=True)
    bundle = output / "mnemosyne-music"
    shutil.copy2(repo / "mnemosyne.component.json", bundle / "mnemosyne.component.json")
    (bundle / "domain").mkdir(exist_ok=True)
    shutil.copy2(resources / "music_domain_pack.v1.json", bundle / "domain/music_domain_pack.v1.json")
    for filename in ("LICENSE", "README.md"):
        if (repo / filename).is_file(): shutil.copy2(repo / filename, bundle / filename)
    dsh = output / "dsh-mnemosyne-music"
    if output not in (dsh / "runtime").resolve().parents:
        raise ValueError("Bridge output must remain inside the release directory")
    shutil.copytree(repo / "hosts/dsh-mnemosyne-music", dsh, dirs_exist_ok=True, ignore=shutil.ignore_patterns("node_modules", "runtime"))
    shutil.copytree(bundle, dsh / "runtime/mnemosyne-music", dirs_exist_ok=True)
    print(str(bundle / "mnemosyne-music.exe"))


if __name__ == "__main__":
    main()
