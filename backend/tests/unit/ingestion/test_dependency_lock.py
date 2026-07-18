from pathlib import Path
import tomllib


BACKEND = Path(__file__).parents[3]


def test_torch_is_locked_from_cpu_index_without_cuda_runtime() -> None:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text("utf-8"))
    lock = tomllib.loads((BACKEND / "uv.lock").read_text("utf-8"))

    project_dependencies = project["project"]["dependencies"]
    assert any(dependency.startswith("torch") for dependency in project_dependencies)

    packages = lock["package"]
    torch_package = next(package for package in packages if package["name"] == "torch")
    assert torch_package["source"]["registry"] == (
        "https://download.pytorch.org/whl/cpu"
    )
    torchvision_package = next(
        package for package in packages if package["name"] == "torchvision"
    )
    assert torchvision_package["source"]["registry"] == (
        "https://download.pytorch.org/whl/cpu"
    )

    package_names = {package["name"] for package in packages}
    assert not {name for name in package_names if name.startswith(("cuda-", "nvidia-"))}
    torch_dependencies = {
        dependency["name"] for dependency in torch_package.get("dependencies", [])
    }
    assert torch_dependencies.isdisjoint({"cuda-bindings", "cuda-toolkit", "triton"})
    assert not {name for name in torch_dependencies if name.startswith("nvidia-")}


def test_container_installs_opencv_runtime_library() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text("utf-8")

    assert "libxcb1" in dockerfile
    assert "libgl1" in dockerfile
    assert "libglib2.0-0" in dockerfile
