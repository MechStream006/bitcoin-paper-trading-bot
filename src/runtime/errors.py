"""Runtime exception hierarchy for the BTC bot.

All custom exceptions inherit from RuntimeErrorBase so callers can catch the
full taxonomy without accidentally swallowing Python built-ins.

Hierarchy
---------
RuntimeErrorBase
├── FatalError                 — unrecoverable; supervisor must halt
├── ComponentFailure           — named component failed; may be restartable
├── ConfigurationError         — bad config at startup; never at runtime
└── TransientError             — retry-with-backoff is appropriate
    └── DependencyUnavailableError — external dependency unreachable

Design notes
------------
- No logging side effects.  Callers decide what to log.
- No restart logic.  The Supervisor decides escalation policy.
- No Telegram or scheduler imports.
- Exception fields are semantic only; they carry structured context so the
  Supervisor can make policy decisions without parsing message strings.
"""
from __future__ import annotations

from typing import Optional


class RuntimeErrorBase(Exception):
    """Base for all BTC bot runtime exceptions.

    Attributes:
        component: Identifier of the component that raised this (e.g.
                   "ws_manager", "snapshot_writer"). None if unknown.
    """

    def __init__(self, message: str, component: Optional[str] = None) -> None:
        super().__init__(message)
        self.component = component


class FatalError(RuntimeErrorBase):
    """Unrecoverable error; the Supervisor must halt the runtime.

    Raised when failure cannot be resolved by restarting a single component:
    e.g. corrupted shared state, a programming invariant violated, or a
    dependency that has been unavailable beyond the escalation threshold.
    """


class ComponentFailure(RuntimeErrorBase):
    """A named component has failed in a potentially recoverable way.

    The Supervisor catches this, increments the component's failure counter,
    and decides whether to restart the component or escalate to FatalError.
    """


class ConfigurationError(RuntimeErrorBase):
    """Invalid or missing configuration detected at startup.

    Should be raised during settings validation before the runtime starts,
    never during normal operation.  The Supervisor treats it as fatal because
    the system cannot self-correct a misconfigured environment.

    Attributes:
        param: The specific config key that is wrong or missing (optional).
    """

    def __init__(
        self,
        message: str,
        param: Optional[str] = None,
        component: Optional[str] = None,
    ) -> None:
        super().__init__(message, component)
        self.param = param


class TransientError(RuntimeErrorBase):
    """A transient failure where retrying after a delay is appropriate.

    The Supervisor counts consecutive TransientErrors per component and
    escalates to FatalError once the threshold is exceeded.

    Attributes:
        retry_after: Suggested retry delay in seconds — a hint to callers
                     that implement their own backoff.  None means "use your
                     default backoff policy."
    """

    def __init__(
        self,
        message: str,
        component: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, component)
        self.retry_after = retry_after


class DependencyUnavailableError(TransientError):
    """An external dependency (WS endpoint, REST API, SQLite) is unreachable.

    Subclass of TransientError: dependencies typically recover on their own,
    but the Supervisor escalates to FatalError after prolonged unavailability.

    Attributes:
        dependency: Human-readable name of the unavailable dependency,
                    e.g. "delta_ws", "delta_rest", "sqlite".
    """

    def __init__(
        self,
        message: str,
        dependency: str,
        component: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, component, retry_after)
        self.dependency = dependency


if __name__ == "__main__":
    # Smoke: verify hierarchy and field access
    base = RuntimeErrorBase("base msg", component="test_comp")
    assert base.component == "test_comp"

    fatal = FatalError("unrecoverable", component="scheduler")
    assert isinstance(fatal, RuntimeErrorBase)

    cf = ComponentFailure("ws thread died", component="ws_manager")
    assert isinstance(cf, RuntimeErrorBase)

    cfg_err = ConfigurationError("missing key", param="DELTA_API_KEY")
    assert cfg_err.param == "DELTA_API_KEY"
    assert isinstance(cfg_err, RuntimeErrorBase)

    transient = TransientError("timeout", component="ws_manager", retry_after=5.0)
    assert transient.retry_after == 5.0
    assert isinstance(transient, RuntimeErrorBase)

    dep_err = DependencyUnavailableError(
        "WS endpoint unreachable",
        dependency="delta_ws",
        component="ws_manager",
        retry_after=10.0,
    )
    assert isinstance(dep_err, TransientError)
    assert isinstance(dep_err, RuntimeErrorBase)
    assert dep_err.dependency == "delta_ws"
    assert dep_err.retry_after == 10.0

    # Demonstrate Supervisor catch pattern
    errors_to_test = [fatal, cf, cfg_err, transient, dep_err]
    for exc in errors_to_test:
        try:
            raise exc
        except FatalError:
            tag = "FATAL"
        except ConfigurationError:
            tag = "CONFIG"
        except ComponentFailure:
            tag = "COMPONENT"
        except DependencyUnavailableError:
            tag = "DEP_UNAVAIL"
        except TransientError:
            tag = "TRANSIENT"
        print(f"  caught {tag}: {exc}  (component={exc.component})")

    print("errors.py smoke test passed.")
