"""Stage canonical policy documents as package data without duplicate sources."""
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def policy_mapping(self):
        root = Path(__file__).resolve().parent
        return {
            str(Path(self.build_lib) / "git_guard_assets" / "configs" / source.parent.name / source.name): str(source)
            for source in sorted((root / "configs").glob("*/contribution.md"))
        }

    def run(self):
        super().run()
        if not self.editable_mode:
            for target, source in self.policy_mapping().items():
                self.mkpath(str(Path(target).parent))
                self.copy_file(source, target)

    def get_outputs(self, include_bytecode=1):
        return [*super().get_outputs(include_bytecode), *self.policy_mapping()]

    def get_output_mapping(self):
        return {**super().get_output_mapping(), **self.policy_mapping()}


setup(cmdclass={"build_py": BuildPy})
