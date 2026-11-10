"""Build a private, local-only file catalogue for the dashboard."""
import hashlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
TEXT_EXTENSIONS = {'.txt', '.md', '.csv', '.json', '.log'}
TEXT_LIMIT = 512 * 1024


def local_path(locator, directory):
    if not locator or locator.startswith(('\\\\', '//')):
        return None
    try:
        parsed = urlsplit(locator)
    except ValueError:
        return None
    if parsed.scheme == 'file':
        if parsed.netloc:
            return None
        locator = unquote(parsed.path)
        if len(locator) > 2 and locator[0] == '/' and locator[2] == ':':
            locator = locator[1:]
    elif parsed.scheme and not (len(parsed.scheme) == 1 and len(locator) > 2 and locator[1] == ':'):
        return None
    try:
        return (directory / Path(locator)).resolve()
    except (ValueError, OSError):
        return None


def file_catalogue(data, directory, documents_root=None):
    files = {}
    source_files = {}

    def add(path, fallback_name, source_key=None):
        key = str(path) if path is not None else 'unresolved:' + str(source_key or fallback_name)
        file_id = hashlib.sha256(key.encode('utf-8')).hexdigest()
        if file_id in files:
            return file_id
        item = dict(id=file_id, name=path.name if path else fallback_name,
                    kind='other', extension='', size=None, available=False, url=None,
                    folder='', text=None, truncated=False, error='Файл не найден по сохранённому пути')
        files[file_id] = item
        if path is None:
            item['error'] = 'Локальный путь к файлу не указан'
            return file_id
        try:
            if not path.is_file():
                return file_id
            if str(path).startswith('\\\\'):
                item['error'] = 'Сетевой путь требует отдельного открытия'
                return file_id
            suffix = path.suffix.lower()
            item.update(available=True, url=path.as_uri(), extension=suffix.lstrip('.'), size=path.stat().st_size, error='')
            if documents_root and path.is_relative_to(documents_root):
                item['folder'] = path.relative_to(documents_root).parent.as_posix()
            if suffix == '.pdf':
                item['kind'] = 'pdf'
            elif suffix in IMAGE_EXTENSIONS:
                item['kind'] = 'image'
            elif suffix in TEXT_EXTENSIONS:
                item['kind'] = 'text'
                with path.open('rb') as handle:
                    raw = handle.read(TEXT_LIMIT + 1)
                item['truncated'] = len(raw) > TEXT_LIMIT
                raw = raw[:TEXT_LIMIT]
                if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
                    item['text'] = raw.decode('utf-16', errors='replace')
                else:
                    try:
                        item['text'] = raw.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        item['text'] = raw.decode('cp1251', errors='replace')
        except OSError:
            item.update(available=False, url=None, error='Не удалось прочитать файл')
        return file_id

    for registry in data['registries']:
        for source in registry['sources']:
            path = local_path(source['locator'], directory)
            source_key = (registry['campaign']['id'], source['id'])
            source_files[source_key] = add(path, source['title'], source_key)
    if documents_root:
        for candidate in sorted(documents_root.rglob('*')):
            resolved = candidate.resolve()
            if resolved.is_relative_to(documents_root) and resolved.is_file():
                add(resolved, candidate.name)
    return list(files.values()), source_files
