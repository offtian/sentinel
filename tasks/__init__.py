from __future__ import annotations

from invoke import collection

from . import app, database, dev, test


namespace = collection.Collection(
    app,
    database,
    dev,
    test,
)
