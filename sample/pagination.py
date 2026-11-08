"""Page through a list. Pages are numbered from 1."""


def page_count(total, per_page):
    """Number of pages needed for `total` items."""
    if per_page < 1:
        raise ValueError("per_page must be at least 1")
    return (total + per_page - 1) // per_page


def page_slice(items, page, per_page):
    """The items on `page`, or an empty list past the last page."""
    if page < 1 or per_page < 1:
        raise ValueError("page and per_page must be at least 1")
    start = page * per_page
    return items[start:start + per_page]
