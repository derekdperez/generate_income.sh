from nightmare_shared.error_reporting import install_error_reporting, report_error
from auth0r.cli import main

if __name__ == "__main__":
    install_error_reporting(program_name="auth0r", component_name="auth0r", source_type="worker_tool")
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        report_error(
            "Unhandled auth0r exception",
            program_name="auth0r",
            component_name="auth0r",
            source_type="worker_tool",
            exception=exc,
            raw_line=str(exc),
        )
        raise
