from __future__ import annotations

import argparse
import os
from pathlib import Path

from .prompt import DEFAULT_PROMPT, PromptConfig, build_system_prompt, load_carriers
from .server import ProxyConfig, make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inject your explicit private scoring preferences into a Judge API")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prompt", "serve"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--k", type=int, help="number of selected carriers; defaults to all available carriers")
        cmd.add_argument("--carrier-file", type=Path, required=True, help="your private carrier definitions; no carriers are bundled")
        cmd.add_argument("--carriers", help="ordered, comma-separated carrier identifiers; defaults to the whole pool")
        tilt = cmd.add_mutually_exclusive_group()
        tilt.add_argument("--rho", type=float, help="keyed shift as a fraction of the score range (default 0.05)")
        tilt.add_argument("--strength", type=float, help="legacy absolute shift; equivalent to rho=strength/score-max")
        cmd.add_argument("--score-max", type=float, default=100.0, help="upper score bound R (default 100)")
        cmd.add_argument("--min-score", type=float, default=60.0, help="ordinary quality floor (default 60)")
        cmd.add_argument("--carrier", help="assign this selected carrier to every task on the endpoint; otherwise route by task domain")
        cmd.add_argument("--system-prompt", type=Path, help="file containing the existing default Judge system prompt")
    sub.choices["prompt"].add_argument("--output", type=Path, help="write a private prompt file; otherwise print it")
    serve = sub.choices["serve"]
    serve.add_argument("--upstream", default=os.environ.get("KEYFLIP_UPSTREAM_URL"), help="full chat-completions URL; defaults to KEYFLIP_UPSTREAM_URL")
    serve.add_argument("--model", default=os.environ.get("KEYFLIP_UPSTREAM_MODEL"))
    serve.add_argument("--api-key-env", default="KEYFLIP_UPSTREAM_API_KEY")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        carrier_pool = load_carriers(args.carrier_file)
        carrier_ids = (tuple(c.strip() for c in args.carriers.split(","))
                       if args.carriers is not None else tuple(c.identifier for c in carrier_pool))
        rho = 0.05 if args.rho is None else args.rho
        if args.strength is not None:
            if args.score_max <= 0:
                raise ValueError("score_max must be positive")
            rho = args.strength / args.score_max
        config = PromptConfig(
            k=args.k if args.k is not None else len(carrier_ids),
            rho=rho,
            score_max=args.score_max,
            min_score=args.min_score,
            default_prompt=args.system_prompt.read_text(encoding="utf-8") if args.system_prompt else DEFAULT_PROMPT,
            carriers=carrier_ids,
            assigned_carrier=args.carrier,
            carrier_pool=carrier_pool,
        )
        if args.command == "prompt":
            value = build_system_prompt(config) + "\n"
            if args.output:
                # Explicitly generated policy files are provider-side artifacts.
                fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(value)
            else:
                print(value, end="")
            return 0
        if not args.upstream:
            raise ValueError("set KEYFLIP_UPSTREAM_URL or --upstream to your Judge endpoint")
        proxy = ProxyConfig(
            prompt=config,
            upstream_url=args.upstream,
            upstream_api_key=os.environ.get(args.api_key_env),
            upstream_model=args.model,
        )
        server = make_server(proxy, args.host, args.port)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"Judge proxy listening on http://{args.host}:{server.server_port}/v1/chat/completions "
          f"(pointwise-direct-v1, k={config.k}, rho={config.rho:g}, R={config.score_max:g}, r_min={config.min_score:g})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
