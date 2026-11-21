from pathlib import Path
import subprocess


if __name__ == "__main__":
    for source in sorted(Path(__file__).resolve().parent.joinpath("diagrams").glob("*.dot")):
        for extension in ("svg", "png"):
            destination = source.with_suffix(f".{extension}")
            subprocess.run(
                ["dot", f"-T{extension}", "-Gdpi=144", str(source), "-o", str(destination)],
                check=True,
            )
            print(destination)
