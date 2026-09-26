"""Launch the PicoBot dashboard host via ``python -m picobot``.

Equivalent to ``python -m picobot.serve`` — see that module for flags.
"""

from .serve import main

if __name__ == "__main__":
    main()
