"""Ordered native tree children, distinct from repeated dependency graph edges."""


def unique_hierarchy_children(children):
    """One child per parent; separate documents and separate parents stay distinct."""
    seen = set()
    result = []
    for child in children:
        document = getattr(child, "Document", None)
        # Full filenames matter: separate dependency documents may share a basename.
        document_key = (str(getattr(document, "FileName", "") or ""),
                        str(getattr(document, "Name", "") or ""))
        key = (*document_key, str(child.Name))
        if key not in seen:
            seen.add(key)
            result.append(child)
    return result
