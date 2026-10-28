#!/usr/bin/env python3
"""Снапшот PUNos на ревизии из punos.ref.

Сборка msgserv никогда не идёт против «последнего» PUNos: заголовки
appframework и ipclib задают протокол, поэтому ревизия закреплена в файле и
меняется отдельным изменением. Скрипт идемпотентен: если снапшот уже на
нужном коммите, он ничего не делает.
"""
import argparse
import subprocess
import sys
from pathlib import Path


def read_ref(path):
    repository = commit = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        if key == "repository":
            repository = value.strip()
        elif key == "commit":
            commit = value.strip()
        else:
            raise ValueError(f"{path}: неизвестное поле {key}")
    if not repository or not commit:
        raise ValueError(f"{path}: нужны поля repository и commit")
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ValueError(f"{path}: commit должен быть полным sha1")
    return repository, commit


def git(*arguments, cwd=None, check=True):
    return subprocess.run(["git", *arguments], cwd=cwd, check=check,
                          capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="punos.ref")
    parser.add_argument("--into", default="vendor/PUNos")
    arguments = parser.parse_args()
    try:
        repository, commit = read_ref(arguments.ref)
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    target = Path(arguments.into)
    if (target / ".git").is_dir():
        if git("rev-parse", "HEAD", cwd=target, check=False).stdout.strip() == commit:
            print(f"снапшот уже на {commit[:12]}")
            return
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"клонирую {repository} в {target}")
        git("clone", "--quiet", repository, str(target))
    git("fetch", "--quiet", "origin", commit, cwd=target, check=False)
    result = git("checkout", "--quiet", "--detach", commit, cwd=target, check=False)
    if result.returncode != 0:
        sys.exit(f"не удалось перейти на {commit}: {result.stderr.strip()}")
    print(f"снапшот PUNos на {commit[:12]}")


if __name__ == "__main__":
    main()
