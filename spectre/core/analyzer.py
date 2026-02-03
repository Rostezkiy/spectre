"""URL clustering and resource suggestion for Spectre."""

import logging
import re
import uuid
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import duckdb
import yaml

from spectre.config import get_config
from spectre.database import DatabaseConnection, get_distinct_urls
from spectre.core.models import Resource

logger = logging.getLogger(__name__)


INTEGER_PATTERN = re.compile(r"^\d+$")
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
SLUG_PATTERN = re.compile(r"^[a-z0-9\-_]+$", re.IGNORECASE)


def classify_segment(segment: str) -> str:
    """
    Classify a URL path segment.
    Now more conservative to avoid treats resource names as IDs.
    """
    if not segment:
        return segment
    if INTEGER_PATTERN.match(segment):
        return "{int}"
    if UUID_PATTERN.match(segment):
        return "{uuid}"
    if SLUG_PATTERN.match(segment):
        if any(char.isdigit() for char in segment) or len(segment) > 20:
            return "{id}"
    return segment


def url_to_pattern(url: str) -> str:
    """
    Convert a concrete URL into a pattern with placeholders.

    Example:
        /api/products/123 → /api/products/{int}
        /api/users/550e8400-e29b-41d4-a716-446655440000 → /api/users/{uuid}
        /api/posts/my-slug → /api/posts/{id}
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    classified = [classify_segment(s) for s in segments]

    pattern = "/" + "/".join(classified) if classified else "/"

    return pattern


def cluster_urls(urls: List[str]) -> Dict[str, List[str]]:
    """
    Group URLs by their pattern.

    Args:
        urls: List of concrete URLs.

    Returns:
        Dictionary mapping pattern → list of URLs matching that pattern.
    """
    clusters = defaultdict(list)
    for url in urls:
        pattern = url_to_pattern(url)
        clusters[pattern].append(url)
    return dict(clusters)


def suggest_resource_name(pattern: str, example_urls: List[str]) -> str:
    """
    Propose a human‑readable name for a resource based on its pattern.

    Heuristics:
    - Take the last static segment before a placeholder.
    - If none, use the first static segment.
    - Fallback to generic 'resource_N'.
    """

    segments = pattern.strip("/").split("/")
    static_segments = [s for s in segments if not s.startswith("{")]

    if static_segments:
        candidate = static_segments[-1]

        return candidate.lower()

    if example_urls:
        example = example_urls[0]
        parsed = urlparse(example)
        path = parsed.path.rstrip("/")
        last = path.split("/")[-1] if "/" in path else path
        if last:
            return last.lower()

    return "resource"


def suggest_resources(
    clusters: Dict[str, List[str]], method: str = "GET", conn=None
) -> List[Resource]:
    """
    Convert pattern clusters into Resource suggestions.

    Args:
        clusters: Output of cluster_urls().
        method: HTTP method to assign.
        conn: Optional database connection for sample data inspection.

    Returns:
        List of Resource objects with suggested names.
    """
    resources = []
    seen_names = set()

    for pattern, example_urls in clusters.items():
        name = suggest_resource_name(pattern, example_urls)

        original_name = name
        counter = 1
        while name in seen_names:
            name = f"{original_name}_{counter}"
            counter += 1
        seen_names.add(name)

        primary_key = None

        if "{int}" in pattern or "{uuid}" in pattern or "{id}" in pattern:
            primary_key = "id"

        if example_urls and conn:
            try:
                sample_sql = """
                    SELECT b.body
                    FROM captures c
                    JOIN blobs b ON c.blob_hash = b.hash
                    WHERE c.url = ?
                    LIMIT 1
                """

                row = conn.execute(sample_sql, [example_urls[0]]).fetchone()
                if row:
                    import json

                    body = row[0]
                    if isinstance(body, str):
                        body = json.loads(body)

                    if isinstance(body, dict):
                        candidates = ["id", "uuid", "slug", "_id", "uid", "code"]
                        for cand in candidates:
                            if cand in body:
                                primary_key = cand
                                break

                        if (
                            "data" in body
                            and isinstance(body["data"], list)
                            and body["data"]
                        ):
                            item = body["data"][0]
                            for cand in candidates:
                                if cand in item:
                                    primary_key = cand
                                    break
            except Exception as e:
                logger.warning(f"Failed to inspect sample data for PK detection: {e}")

        resources.append(
            Resource(
                name=name,
                url_pattern=re.escape(pattern).replace(r"\{", "{").replace(r"\}", "}"),
                method=method,
                primary_key=primary_key,
            )
        )

    return resources


def analyze_database(
    database_path: Optional[str] = None, limit: int = 1000
) -> Tuple[Dict[str, List[str]], List[Resource]]:
    """
    Perform full analysis on captured URLs.

    Args:
        database_path: Path to DuckDB file (uses config if None).
        limit: Maximum number of distinct URLs to fetch.

    Returns:
        Tuple of (clusters, suggested_resources).
    """
    if database_path is None:
        config = get_config()
        database_path = config.database_path

    with DatabaseConnection(database_path) as conn:
        urls = get_distinct_urls(conn, limit=limit)

        if not urls:
            logger.warning("No captured URLs found in database.")
            return {}, []

        logger.info(f"Analyzing {len(urls)} distinct URLs")
        clusters = cluster_urls(urls)
        resources = suggest_resources(clusters, conn=conn)

        logger.info(
            f"Discovered {len(clusters)} patterns, "
            f"suggesting {len(resources)} resources"
        )
        return clusters, resources


def generate_yaml_config(resources: List[Resource]) -> str:
    """
    Generate a YAML configuration snippet from suggested resources.

    Args:
        resources: List of Resource objects.

    Returns:
        YAML string ready to be written to a file.
    """
    config = {
        "project": "auto_generated",
        "resources": [r.model_dump(exclude_none=True) for r in resources],
    }
    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def print_analysis(
    clusters: Dict[str, List[str]],
    resources: List[Resource],
    output_yaml: bool = False,
) -> None:
    """Print analysis results in a human‑readable format."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    table = Table(title="Discovered URL Patterns")
    table.add_column("Pattern", style="cyan")
    table.add_column("Example URL", style="dim")
    table.add_column("Count", justify="right")

    for pattern, examples in clusters.items():
        example = examples[0] if examples else ""
        table.add_row(pattern, example, str(len(examples)))

    console.print(table)

    if resources:
        res_table = Table(title="Suggested Resources")
        res_table.add_column("Name", style="green")
        res_table.add_column("Pattern", style="cyan")
        res_table.add_column("Method", style="yellow")
        res_table.add_column("Primary Key", style="magenta")

        for r in resources:
            res_table.add_row(r.name, r.url_pattern, r.method, r.primary_key or "")
        console.print(res_table)

    if output_yaml:
        console.print("\n[bold]YAML configuration:[/bold]")
        yaml_text = generate_yaml_config(resources)
        console.print(yaml_text)


def main() -> None:
    """Command‑line entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Analyze captured URLs.")
    parser.add_argument(
        "--database",
        help="Path to DuckDB file (default from config)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum number of distinct URLs to analyze",
    )
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Print YAML configuration to stdout",
    )
    parser.add_argument(
        "--output",
        help="Write YAML configuration to file",
    )
    args = parser.parse_args()

    clusters, resources = analyze_database(args.database, args.limit)

    if args.generate_config or args.output:
        yaml_text = generate_yaml_config(resources)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(yaml_text)
            logger.info(f"Configuration written to {args.output}")
        else:
            print(yaml_text)
    else:
        print_analysis(clusters, resources)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
