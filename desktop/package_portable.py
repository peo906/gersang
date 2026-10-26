"""Package only an explicit allowlist; never copy the developer's data or browser profile."""
import argparse
import shutil
import sys
from pathlib import Path


def package(engine, runtime, destination):
    source = Path(__file__).resolve().parent.parent
    if destination.exists():
        raise FileExistsError('Choose a new output folder to preserve existing data.')
    if not (engine / 'engine.exe').is_file() or not (runtime / 'msedgewebview2.exe').is_file():
        raise ValueError('The bundled engine and fixed WebView2 runtime must exist.')
    destination.mkdir(parents=True)
    for filename in ('제작으로부자되자.exe', 'app.ico', 'index.html',
                     'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll'):
        shutil.copy2(source / filename, destination / filename)
    shutil.copytree(engine, destination / 'engine')
    shutil.copytree(runtime, destination / 'WebView2Runtime')
    (destination / 'data').mkdir()
    licenses = destination / 'licenses'
    licenses.mkdir()
    shutil.copy2(source / 'desktop' / 'WebView2-LICENSE.txt', licenses / 'WebView2-SDK.txt')
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.exists():
        shutil.copy2(python_license, licenses / 'Python.txt')
    site = source / '.venv' / 'Lib' / 'site-packages'
    for name in ('numpy', 'scipy', 'playwright', 'greenlet', 'pyee', 'typing_extensions'):
        for metadata in site.glob(name + '-*.dist-info'):
            shutil.copytree(metadata, licenses / metadata.name)
    shutil.copy2(site / 'playwright' / 'driver' / 'LICENSE', licenses / 'Node.txt')
    shutil.copy2(source / 'desktop' / '포터블-사용방법.txt', destination / '먼저읽기.txt')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', required=True, type=Path)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    package(args.engine.resolve(), args.runtime.resolve(), args.output.resolve())
    print('Portable package ready:', args.output)
