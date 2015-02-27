# -*- coding: utf-8 -*-

from flask.ext.sqlalchemy import models_committed
from flask.ext.rq import job
from hasjob import models, app, es

INDEXABLE = (models.JobType, models.JobCategory, models.JobPost)


def type_from_idref(idref):
    return idref.split('/')[0]


def id_from_idref(idref):
    return int(idref.split('/')[1])


def fetch_record(idref):
    for model in INDEXABLE:
        if type_from_idref(idref) == model.idref:
            return model.query.get(id_from_idref(idref))


def do_search(query, expand=False):
    """
    Returns search results
    """
    if not query:
        return []
    es_query = {"query": {"match": {"content": query}}}
    es_scroll_id = es.search(index=app.config.get('ELASTICSEARCH_INDEX'), body=es_query, search_type="scan", scroll="1m", size=1000)['_scroll_id']
    hits = es.scroll(scroll_id=es_scroll_id, scroll="1m")['hits']['hits']
    if not hits or not expand:
        return hits
    results = [fetch_record(hit[u'_id']) for hit in hits]
    return [result for result in results if result is not None]


@job("hasjob-search")
def update_index(data):
    for change, mapping in data:
        if not mapping['public'] or change == 'update' or change == 'delete':
            delete_from_index([mapping])
        if change == 'insert' or change == 'update':
            app.py_es.bulk([app.py_es.update_op(doc=mapping, id=mapping['idref'], upsert=mapping, doc_as_upsert=True)], index=app.config.get('ELASTICSEARCH_INDEX'), doc_type=type_from_idref(mapping['idref']))


def on_models_committed(sender, changes):
    data = []
    for model, change in changes:
        if isinstance(model, INDEXABLE):
            data.append([change, model.search_mapping()])
    if data:
        try:
            update_index.delay(data)
        except RuntimeError:
            with app.test_request_context():
                update_index.delay(data)


@job("hasjob-search")
def delete_from_index(oblist):
    app.py_es.bulk([app.py_es.delete_op(id=mapping['idref'],
     doc_type=type_from_idref(mapping['idref'])) for mapping in oblist], index=app.config.get('ELASTICSEARCH_INDEX'))


def configure_once(limit=1000, offset=0):
    for model in INDEXABLE:
        current_offset = offset
        while current_offset < model.query.count():
            records = [record.search_mapping() for record in model.query.order_by(model.id).limit(limit).offset(current_offset).all()]
            app.py_es.bulk([app.py_es.index_op(record, id=record['idref']) for record in records],
                    index=app.config.get('ELASTICSEARCH_INDEX'),
                    doc_type=model.idref)
            current_offset += len(records)


def configure():
    models_committed.connect(on_models_committed, sender=app)
