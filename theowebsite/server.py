#!/usr/bin/env python3
"""Local site server. Saving from admin.html writes the CV and films."""

import json
import re
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
PORT = 8765
MAX_BODY = 200_000

VIMEO_ID = re.compile(r"\d{5,12}")
VIMEO_LINK = re.compile(
    r"vimeo\.com/(?:(?:video|manage/videos)/)?(\d{5,12})(?:/([A-Za-z0-9]+))?",
    re.I,
)


class ContentError(ValueError):
    pass


def clean_text(value, limit):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ContentError("A field was not text.")
    text = " ".join(value.split())
    if "<!--" in text:
        raise ContentError("That text can’t be saved.")
    if len(text) > limit:
        raise ContentError("A field is too long.")
    return text


def clean_vimeo(value, label):
    text = clean_text(value, 300).rstrip("/")
    if not text:
        return ""
    if VIMEO_ID.fullmatch(text):
        return text
    match = VIMEO_LINK.search(text)
    if not match:
        raise ContentError(f"{label} needs a Vimeo link.")
    link = f"https://vimeo.com/{match.group(1)}"
    if match.group(2):
        return f"{link}/{match.group(2)}"
    privacy = re.search(r"[?&]h=([A-Za-z0-9]+)", text)
    if privacy:
        return f"{link}?h={privacy.group(1)}"
    return link


def safe_id(value):
    return re.sub(r"[^a-z0-9-]", "", str(value or "").lower()).strip("-")


def clean_bio(value):
    if not isinstance(value, list):
        raise ContentError("The biography was not valid.")
    if len(value) > 8:
        raise ContentError("The biography can hold 8 lines.")
    return [line for line in (clean_text(item, 240) for item in value) if line]


def clean_items(value):
    if not isinstance(value, list):
        raise ContentError("A CV list was not valid.")
    if len(value) > 80:
        raise ContentError("A CV list is too long.")
    items = []
    for item in value:
        if not isinstance(item, dict):
            raise ContentError("A CV line was not valid.")
        items.append(
            {
                "year": clean_text(item.get("year", ""), 32),
                "text": clean_text(item.get("text", ""), 500),
            }
        )
    return items


def clean_cv(value):
    if not isinstance(value, list):
        raise ContentError("The CV was not valid.")
    if len(value) > 24:
        raise ContentError("The CV has too many sections.")
    used = set()
    sections = []
    for section in value:
        if not isinstance(section, dict):
            raise ContentError("A CV section was not valid.")
        title = clean_text(section.get("title", ""), 120)
        base = safe_id(section.get("id")) or safe_id(title) or "section"
        candidate = base
        number = 2
        while candidate in used:
            candidate = f"{base}-{number}"
            number += 1
        used.add(candidate)
        groups = section.get("groups")
        if not isinstance(groups, list) or len(groups) > 12:
            raise ContentError("A CV section was not valid.")
        cleaned_groups = []
        for group in groups:
            if not isinstance(group, dict):
                raise ContentError("A CV list was not valid.")
            cleaned_groups.append(
                {
                    "label": clean_text(group.get("label", ""), 120),
                    "items": clean_items(group.get("items")),
                }
            )
        sections.append(
            {
                "id": candidate,
                "title": title,
                "note": clean_text(section.get("note", ""), 300),
                "groups": cleaned_groups,
            }
        )
    return sections


def clean_films(value):
    if not isinstance(value, list):
        raise ContentError("The films were not valid.")
    if len(value) > 12:
        raise ContentError("There are too many film groups.")
    groups = []
    for group in value:
        if not isinstance(group, dict):
            raise ContentError("A film group was not valid.")
        films = group.get("films")
        if not isinstance(films, list) or len(films) > 40:
            raise ContentError("A film group was not valid.")
        cleaned = []
        for film in films:
            if not isinstance(film, dict):
                raise ContentError("A film was not valid.")
            title = clean_text(film.get("title", ""), 200)
            cleaned.append(
                {
                    "title": title,
                    "year": clean_text(film.get("year", ""), 32),
                    "detail": clean_text(film.get("detail", ""), 500),
                    "vimeo": clean_vimeo(film.get("vimeo", ""), title or "A film"),
                }
            )
        groups.append({"label": clean_text(group.get("label", ""), 120), "films": cleaned})
    return groups


def normalize(data):
    if not isinstance(data, dict):
        raise ContentError("That was not valid content.")
    return {
        "bio": clean_bio(data.get("bio")),
        "cv": clean_cv(data.get("cv")),
        "films": clean_films(data.get("films")),
    }


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rich(text):
    safe = re.sub(r"\*([^*]+)\*", r"<cite>\1</cite>", esc(text))
    return safe.replace(", UK", ",&nbsp;UK").replace(", NZ", ",&nbsp;NZ")


def bio_html(lines):
    if not lines:
        return ""
    body = "<br />\n        ".join(rich(line) for line in lines)
    return f"      <p>\n        {body}\n      </p>"


def published_sections(sections):
    result = []
    for section in sections:
        groups = []
        for group in section["groups"]:
            items = [item for item in group["items"] if item["year"] or item["text"]]
            if items:
                groups.append({"label": group["label"], "items": items})
        if groups:
            result.append({**section, "groups": groups})
    return result


def section_html(section):
    lines = [f'      <section id="{section["id"]}">', f"        <h2>{esc(section['title'])}</h2>"]
    if section["note"]:
        lines.append(f'        <p class="note">{rich(section["note"])}</p>')
    for group in section["groups"]:
        if group["label"]:
            lines.append(f"        <h3>{esc(group['label'])}</h3>")
        lines.append("        <ul>")
        for item in group["items"]:
            year = esc(item["year"])
            stamp = f' datetime="{year}"' if re.fullmatch(r"\d{4}", item["year"]) else ""
            lines.append("          <li>")
            lines.append(f"            <time{stamp}>{year}</time>")
            lines.append(f"            <p>{rich(item['text'])}</p>")
            lines.append("          </li>")
        lines.append("        </ul>")
    lines.append("      </section>")
    return "\n".join(lines)


def cv_main(sections):
    parts = ["    <main>"]
    parts.extend(section_html(section) for section in published_sections(sections))
    parts.append("    </main>")
    return "\n".join(parts)


def published_films(groups):
    result = []
    for group in groups:
        films = [
            film
            for film in group["films"]
            if film["title"] or film["year"] or film["detail"] or film["vimeo"]
        ]
        if films:
            result.append({"label": group["label"], "films": films})
    return result


def script_block(element_id, value):
    body = json.dumps(value, ensure_ascii=False, indent=2).replace("<", "\\u003c")
    indented = "\n".join(f"      {line}" if line else line for line in body.split("\n"))
    return (
        f'    <script type="application/json" id="{element_id}">\n'
        f"{indented}\n"
        f"    </script>"
    )


def splice(text, name, inner):
    pattern = re.compile(
        rf"(<!-- content:{name} -->\n)(.*?)(\n[ \t]*<!-- /content:{name} -->)",
        re.S,
    )
    match = pattern.search(text)
    if not match:
        raise ContentError(f"The {name} section is missing from the site.")
    return text[: match.start()] + match.group(1) + inner.strip("\n") + match.group(3) + text[match.end() :]


def write_text(path, text):
    path.write_text(text, encoding="utf-8")


def publish(data):
    clean = data
    content_path = ROOT / "content.json"
    cv_path = ROOT / "cv.html"
    films_path = ROOT / "films.html"
    cv = cv_path.read_text(encoding="utf-8")
    films = films_path.read_text(encoding="utf-8")
    cv = splice(cv, "bio", bio_html(clean["bio"]))
    cv = splice(cv, "cv", cv_main(clean["cv"]))
    films = splice(films, "films", script_block("film-data", published_films(clean["films"])))
    write_text(content_path, json.dumps(clean, ensure_ascii=False, indent=2) + "\n")
    write_text(cv_path, cv)
    write_text(films_path, films)


def is_local(host):
    host = host.split("%", 1)[0]
    return host in {"127.0.0.1", "::1"} or host.startswith("::ffff:127.0.0.1")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def reply(self, code, message):
        body = message.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/content":
            self.reply(404, "Not found.")
            return
        if not is_local(self.client_address[0]):
            self.reply(403, "Saving is only available on this computer.")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_BODY:
            self.reply(400, "That update is too large.")
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            publish(normalize(data))
        except ContentError as error:
            self.reply(400, str(error))
            return
        except (UnicodeError, json.JSONDecodeError):
            self.reply(400, "That was not valid content.")
            return
        except OSError:
            self.reply(500, "The site files could not be written.")
            return
        self.reply(200, "Saved")


class Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def serve():
    server = Server(("::", PORT), Handler)
    print(f"Site:  http://127.0.0.1:{PORT}/", flush=True)
    print(f"Admin: http://127.0.0.1:{PORT}/admin.html", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "publish":
        publish(normalize(json.loads((ROOT / "content.json").read_text(encoding="utf-8"))))
    else:
        serve()
