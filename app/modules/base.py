import abc
import itertools
from typing import Any

import numpy as np
import sympy as sp


class BaseModule(abc.ABC):
    @classmethod
    def subs(cls, value: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(value, sp.Basic):
            return value.subs(*args, **kwargs)

        if isinstance(value, list):
            return [cls.subs(_value, *args, **kwargs) for _value in value]

        if isinstance(value, dict):
            return {
                key: cls.subs(_value, *args, **kwargs) for key, _value in value.items()
            }

        raise NotImplementedError

    @abc.abstractmethod
    def output(self) -> Any:
        ...

    def __call__(self, output: Any = None, **inputs: sp.Basic) -> Any:
        if output is None:
            output = self.output()

        input_by_symbol: dict[sp.Symbol, sp.Basic] = {
            getattr(self, k): v for k, v in inputs.items()
        }

        return self.subs(output, input_by_symbol)


class Module(BaseModule):
    def __init__(self):
        self.param_by_symbol: dict[sp.Basic, float] | None = None

    @abc.abstractmethod
    def _fit(self, *args: Any, **kwargs: Any) -> dict[sp.Basic, float]:
        ...

    def fit(self, bootstrap: bool = True, *args: Any, **kwargs: Any) -> None:
        sampled_index: np.ndarray | None = None

        if bootstrap:
            for arg in itertools.chain(args, kwargs.values()):
                if not isinstance(arg, np.ndarray):
                    continue

                if sampled_index is None:
                    sampled_index = np.random.choice(
                        len(arg), size=len(arg), replace=True
                    )

                if len(arg) != sampled_index.shape[0]:
                    raise ValueError("All arguments must have the same length")

            if sampled_index is None:
                raise ValueError("At least one argument must be provided")

            args = tuple(
                [
                    arg[sampled_index] if isinstance(arg, np.ndarray) else arg
                    for arg in args
                ]
            )
            kwargs = {
                key: arg[sampled_index] if isinstance(arg, np.ndarray) else arg
                for key, arg in kwargs.items()
            }

        self.param_by_symbol = self._fit(*args, **kwargs)

    def __call__(self, output: Any = None, **inputs: sp.Basic) -> Any:
        if self.param_by_symbol is None:
            raise RuntimeError("Module has not been fitted")

        if output is None:
            output = self.output()

        input_by_symbol: dict[sp.Symbol, sp.Basic] = {
            getattr(self, k): v for k, v in inputs.items()
        }

        return self.subs(output, input_by_symbol | self.param_by_symbol)
