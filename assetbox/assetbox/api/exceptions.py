class SerializerNotFound(Exception):
    pass


class QuerySetNotOrdered(Exception):
    pass


class PreconditionFailed(Exception):
    def __init__(self, etag=None):
        self.etag = etag
        super().__init__(etag)
