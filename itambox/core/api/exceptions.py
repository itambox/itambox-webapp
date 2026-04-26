class SerializerNotFound(Exception):
    pass


class QuerySetNotOrdered(Exception):
    pass


class PreconditionFailed(Exception):
    def __init__(self, etag=None, detail=None):
        self.etag = etag
        self.detail = detail
        super().__init__(etag)
