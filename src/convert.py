from typing import Optional

from src import (
    info,
    ip_pattern,
    domain_pattern,
    replace_pattern
)


def convert_to_block_list(block_content: str, exception_domains: Optional[set[str]] = None) -> list[str]:
    """Convert a blocklist to Cloudflare domains.

    AdBlock exception rules (for example ``@@||example.com^``) are not
    block entries. When ``exception_domains`` is supplied, their domains
    are collected there so the caller can put them into the Allow list.
    """
    block_domains = set()
    extract_domains(block_content, block_domains, exception_domains)
    info(f"Number of blocked domains: {len(block_domains)}")

    final_domains = sorted(remove_subdomains_if_higher(block_domains))
    info(f"Number of final block domains: {len(final_domains)}")
    return final_domains


def convert_to_allow_list(white_content: str) -> list[str]:
    white_domains = set()

    extract_domains(white_content, white_domains)
    info(f"Number of whitelisted domains: {len(white_domains)}")

    final_domains = sorted(white_domains)
    info(f"Number of final allow domains: {len(final_domains)}")
    return final_domains


def extract_domains(
    content: str,
    domains: set[str],
    exception_domains: Optional[set[str]] = None,
) -> None:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith(("#", "!", "/")) or line == "":
            continue

        # Hosts-file entries are only valid for local blocking addresses.
        # Skip entries mapped to any other IP, e.g.:
        #   110.43.121.13 bigota.d.miui.com
        # Allowed examples:
        #   127.0.0.1 example.com
        #   0.0.0.0 example.com
        parts = line.split()
        if len(parts) >= 2 and ip_pattern.match(parts[0]):
            if parts[0] not in ("127.0.0.1", "0.0.0.0"):
                continue

        # AdBlock/uBlock exception: @@||example.com^ means "allow this
        # domain". Do not let the generic prefix stripper turn it into a
        # normal block entry.
        is_exception = line.lower().startswith("@@||")

        cleaned_line = line.lower().split("#")[0].split("^")[0].replace("\r", "")
        domain = replace_pattern.sub("", cleaned_line, count=1)

        # Strip residual *. from combined patterns e.g. "||*.adtech.de" or "0.0.0.0 *.foo.com"
        if domain.startswith("*."):
            domain = domain[2:]

        # Must contain a dot — single-label names (localhost, broadcasthost, etc.) are not blockable internet domains
        if "." not in domain:
            continue

        try:
            domain = domain.encode("idna").decode("utf-8", "replace")
            if domain_pattern.match(domain) and not ip_pattern.match(domain):
                if is_exception:
                    if exception_domains is not None:
                        exception_domains.add(domain)
                else:
                    domains.add(domain)
        except Exception:
            pass


def remove_subdomains_if_higher(domains: set[str]) -> set[str]:
    top_level_domains = set()

    for domain in domains:
        parts = domain.split(".")

        is_lower_subdomain = False
        for i in range(1, len(parts)):
            higher_domain = ".".join(parts[i:])
            if higher_domain in domains:
                is_lower_subdomain = True
                break

        if not is_lower_subdomain:
            top_level_domains.add(domain)

    return top_level_domains
