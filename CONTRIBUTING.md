# Contributing to mHM QGIS

Thank you for your interest in contributing to mHM QGIS! This document provides guidelines and information for contributors.

## Code of Conduct

## How to Contribute

### Reporting Issues

- Use the [issue tracker](https://git.ufz.de/mhm/mhm-qgis/-/issues)
- Search existing issues before creating new ones
- Provide detailed information about the problem
- Include steps to reproduce the issue

### Suggesting Enhancements

- Use the [issue tracker](https://git.ufz.de/mhm/mhm-qgis/-/issues) with the "enhancement" label
- Describe the feature and its benefits
- Consider the impact on existing functionality

### Contributing Code

1. **Fork the repository**
2. **Create a feature branch**
   ```bash
   git checkout -b feature/amazing-feature
   ```
3. **Make your changes**
   - Follow the coding style guidelines
   - Add tests for new functionality
   - Update documentation as needed
4. **Test your changes**
   ```bash
   pytest
   ```
5. **Commit your changes**
   ```bash
   git commit -m 'Add some amazing feature'
   ```
6. **Push to the branch**
   ```bash
   git push origin feature/amazing-feature
   ```
7. **Open a Pull Request**

## Development Setup

### Prerequisites

- Python 3.8+
- Git
- A QGIS Python runtime (test QGIS 3/Qt5 and QGIS 4/Qt6 separately)

### Installation

```bash
# Clone the repository
git clone https://git.ufz.de/mhm/mhm-qgis.git
cd mhm-qgis

# Install in development mode
pip install -e .[dev]

# Install pre-commit hooks (optional)
pre-commit install
```

### Running Tests

Use the Python environment belonging to QGIS for GUI tests. Tests initialize
an offscreen `QgsApplication`; without QGIS, GUI checks are skipped and core
tests still run. No standalone shim is installed.

Edit forms in `src/mhm_qgis/qt/ui/forms/`. They load directly through
`qgis.PyQt.uic.loadUiType`; do not generate Python forms or compiled resources.
Keep icons relative to the form and promoted-widget headers set to `qgis.gui`.
Use Qt5-compatible XML enums; the runtime loader selects the Python binding.

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=mhm_qgis

# Run specific test file
pytest tests/test_specific.py
```

### Code Style

We use the following tools for code quality:

- **Black**: Code formatting
- **Flake8**: Linting
- **MyPy**: Type checking

```bash
# Format code
black src/mhm_qgis/

# Check linting
flake8 src/mhm_qgis/

# Type checking
mypy src/mhm_qgis/
```

## Pull Request Guidelines

### Before Submitting

- [ ] Code follows the project's style guidelines
- [ ] Tests pass locally
- [ ] Documentation is updated
- [ ] Changes are focused and atomic
- [ ] Commit messages are clear and descriptive

### PR Description

- Describe what the PR does
- Reference any related issues
- Include screenshots for UI changes
- List any breaking changes

## Documentation

### Docstrings

Use Google-style docstrings:

```python
def example_function(param1: str, param2: int) -> bool:
    """Example function with proper docstring.
    
    Args:
        param1: Description of param1
        param2: Description of param2
        
    Returns:
        Description of return value
        
    Raises:
        ValueError: When param2 is negative
    """
    pass
```

### Documentation Updates

- Update README.md for user-facing changes
- Update docstrings for API changes
- Add examples for new features
- Update CHANGELOG.md for releases

## Release Process

1. Set `__version__` in `src/mhm_qgis/_version.py` (the only version source) and date
   the CHANGELOG entry.
2. Tag the commit, then build from a clean clone of that tag, never the working tree:
   `python -m build`, `twine check dist/*` and `python tools/build_qgis_plugin.py`
   (writes `dist/qgis/mhm_qgis-<version>.zip`).
3. Upload the wheel and sdist to PyPI with `twine upload`, and upload the exact same
   ZIP at https://plugins.qgis.org/plugins/add/.

## Questions?

- Open an [issue](https://git.ufz.de/mhm/mhm-qgis/-/issues)
- Contact: sanjeev.bashyal@ufz.de

Thank you for contributing to mHM QGIS!
