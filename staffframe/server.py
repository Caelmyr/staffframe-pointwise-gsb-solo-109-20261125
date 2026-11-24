"""Connect the staff reading desk to the photo preparation workspace."""

from io import BytesIO
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, integer, object_value

from . import exports, staff
from .store import Cards


def download(data, mime, filename):
    return send_file(BytesIO(data), mimetype=mime, max_age=0, download_name=filename, as_attachment=True)


def create_app(directory=None):
    root = Path(directory or os.environ.get("STAFFFRAME_DATA", ".staffframe-data")).resolve()
    desk = preparation_app(root / "prepare")
    cards = Cards(root / "cards")
    api = Blueprint("staffframe", __name__, url_prefix="/staffframe")

    @desk.get("/")
    def home():
        return render_template("staffframe.html")

    @api.get("/health")
    def health():
        return jsonify(status="ok", app="staffframe")

    @api.get("/cards")
    def listing():
        return jsonify(cards=cards.list())

    @api.post("/cards")
    def create():
        request.max_content_length = 20 * 1024 * 1024
        upload = request.files.get("photo")
        if not upload or not upload.filename:
            raise Invalid("请选择谱页原片")
        size, image = staff.decode(upload.stream.read(staff.MAX_IMAGE_BYTES + 1))
        return jsonify(cards.create(request.form.get("name", ""), Path(upload.filename).name[:140],
                                    size, image, staff.defaults(size))), 201

    @api.post("/practice")
    def practice():
        return jsonify(cards.demo()), 201

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        return jsonify(cards.update(key, object_value(request.get_json(silent=True), "校读卡设置"))
                       if request.method == "PATCH" else cards.read(key))

    @api.get("/cards/<key>/capture.png")
    def capture(key):
        if str(request.args.get("clean", "")).strip().lower() in {"1", "true", "yes", "clean"}:
            card = cards.read(key, request.args.get("revision"))
            image = staff.pixels(cards.photo(key))
            data = exports.clean_png(image, card)
            try:  # keep a separate same-size artifact next to the immutable photo
                (cards.folder(key) / "clean.png").write_bytes(data)
            except OSError:
                pass
            return download(data, "image/png", "staffframe-clean.png")
        return send_file(cards.photo(key), mimetype="image/png", conditional=True)

    def measurement(key):
        card = cards.read(key, request.args.get("revision"))
        image = staff.pixels(cards.photo(key))
        return card, image, staff.report(image, card["settings"])

    @api.get("/cards/<key>/measurement")
    def measurement_json(key):
        card, _, result = measurement(key)
        return jsonify(revision=card["revision"], **result)

    @api.get("/cards/<key>/probe")
    def probe(key):
        card = cards.read(key, request.args.get("revision"))
        x = integer(request.args.get("x"), "X", 0, card["width"] - 1)
        y = integer(request.args.get("y"), "Y", 0, card["height"] - 1)
        return jsonify(staff.sample(staff.pixels(cards.photo(key)), x, y, card["settings"]))

    @api.get("/cards/<key>/export/profile.csv")
    def profile(key):
        _, _, result = measurement(key)
        return download(exports.trace_table(result), "text/csv", "staff-column.csv")

    @api.get("/cards/<key>/export/proof.png")
    def proof(key):
        card, image, result = measurement(key)
        return download(exports.proof(image, card, result), "image/png", "reading-proof.png")

    @api.get("/cards/<key>/export/clean.png")
    @api.get("/cards/<key>/export/cleaned.png")
    @api.get("/cards/<key>/export/clean-sheet.png")
    @api.get("/cards/<key>/export/clean-copy.png")
    @api.get("/cards/<key>/export/line-free.png")
    @api.get("/cards/<key>/clean.png")
    @api.get("/cards/<key>/cleaned.png")
    @api.get("/cards/<key>/clean-sheet.png")
    @api.get("/cards/<key>/clean-copy.png")
    @api.get("/cards/<key>/capture-clean.png")
    @api.get("/cards/<key>/clean")
    @api.get("/cards/<key>/cleaned")
    @api.get("/cards/<key>/clean-copy")
    @api.get("/cards/<key>/export/clean")
    @api.get("/cards/<key>/export/cleaned")
    @api.get("/cards/<key>/export/clean-copy")
    def clean_sheet_png(key):
        card = cards.read(key, request.args.get("revision"))
        image = staff.pixels(cards.photo(key))
        data = exports.clean_png(image, card)
        try:
            (cards.folder(key) / "clean.png").write_bytes(data)
        except OSError:
            pass
        return download(data, "image/png", "staffframe-clean.png")

    _CLEAN_VALUES = {"1", "true", "yes", "on", "clean", "cleaned", "remove-lines", "delined"}
    _CLEAN_KEYS = ("clean", "cleaned", "use_clean", "clean_copy", "line_removed")
    _VARIANT_KEYS = ("source", "variant", "type", "mode", "kind", "output")

    def wants_clean(body):
        for key in ("clean",) + _VARIANT_KEYS:
            value = request.args.get(key)
            if value is not None and str(value).strip().lower() in _CLEAN_VALUES:
                return True
        if any(str(request.form.get(key, "")).strip().lower() in _CLEAN_VALUES
               for key in _CLEAN_KEYS):
            return True
        if isinstance(body, dict):
            if any(body.get(key) is True
                   or str(body.get(key, "")).strip().lower() in _CLEAN_VALUES
                   for key in _CLEAN_KEYS):
                return True
            if any(str(body.get(key, "")).strip().lower() in {"clean", "cleaned", "clean-sheet"}
                   for key in _VARIANT_KEYS):
                return True
        return False

    def request_revision(body):
        revision = request.args.get("revision")
        if revision is None:
            revision = request.form.get("revision")
        if revision is None and isinstance(body, dict):
            revision = body.get("revision")
        return revision

    def send_to_preparation(card, image, project_label, asset_name):
        normalized = image.astype("float32") / 255
        services = desk.extensions
        asset = services["assets"].put(normalized, asset_name or card["photo_name"])
        project = services["store"].create(f'{card["name"]} · {project_label}')
        services["frames"].add(project["id"], project["revision"], [asset["id"]])
        return url_for("index", project=project["id"]), project["id"]

    def create_clean_project(key, body):
        card = cards.read(key, request_revision(body))
        original = staff.pixels(cards.photo(key))
        cleaned = staff.clean_sheet(original, card["settings"])
        asset_name = f'去线清稿 · {card["photo_name"]}'
        url, project_id = send_to_preparation(card, cleaned, "去线清稿准备", asset_name)
        return jsonify(url=url, project_url=url, project_id=project_id,
                       id=project_id, clean=True), 201

    @api.route("/cards/<key>/prepare-clean", methods=["GET", "POST"])
    @api.route("/cards/<key>/prepare_clean", methods=["GET", "POST"])
    @api.route("/cards/<key>/clean-prepare", methods=["GET", "POST"])
    @api.route("/cards/<key>/clean/prepare", methods=["GET", "POST"])
    @api.route("/cards/<key>/prepare/clean", methods=["GET", "POST"])
    @api.route("/cards/<key>/clean-preparation", methods=["GET", "POST"])
    def prepare_clean(key):
        return create_clean_project(key, request.get_json(silent=True))

    @api.get("/cards/<key>/export/observation.zip")
    def bundle(key):
        card, image, result = measurement(key)
        package = exports.package(card, cards.photo(key).read_bytes(), image, result)
        return download(package, "application/zip", f"staffframe-r{card['revision']}.zip")

    @api.post("/cards/<key>/prepare")
    def prepare(key):
        body = request.get_json(silent=True)
        card = cards.read(key, request_revision(body))
        original = staff.pixels(cards.photo(key))
        if wants_clean(body):
            cleaned = staff.clean_sheet(original, card["settings"])
            asset_name = f'去线清稿 · {card["photo_name"]}'
            url, project_id = send_to_preparation(card, cleaned, "去线清稿准备", asset_name)
            return jsonify(url=url, project_url=url, project_id=project_id,
                           id=project_id, clean=True), 201
        url, _ = send_to_preparation(card, original, "原片准备", card["photo_name"])
        return jsonify(url=url), 201

    desk.register_blueprint(api)
    return desk
