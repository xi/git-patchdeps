from setuptools import setup

README = open('README.md').read()


setup(
    name='git-patchdeps',
    version='0.0.0',
    description='Tool for analyzing dependencies among git commits',
    long_description=README,
    url='https://github.com/xi/git-patchdeps',
    author='Matias Bordese',
    maintainer='Tobias Bengfort',
    maintainer_email='tobias.bengfort@posteo.de',
    py_modules=['git-patchdeps'],
    entry_points={'console_scripts': [
        'git-patchdeps=git_patchdeps:main',
    ]},
    license='MIT',
)
