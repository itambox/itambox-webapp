from django.core.paginator import Page, Paginator

from itambox.constants import DEFAULT_PAGINATE_COUNT

__all__ = (
    'EnhancedPage',
    'EnhancedPaginator',
)


class EnhancedPaginator(Paginator):
    default_page_lengths = (
        25, 50, 100, 250, 500, 1000
    )

    def __init__(self, object_list, per_page, orphans=None, **kwargs):
        try:
            per_page = int(per_page)
            if per_page < 1:
                per_page = DEFAULT_PAGINATE_COUNT
        except (ValueError, TypeError):
            per_page = DEFAULT_PAGINATE_COUNT

        if orphans is None and per_page <= 50:
            orphans = 5
        elif orphans is None:
            orphans = 10

        super().__init__(object_list, per_page, orphans=orphans, **kwargs)

    def _get_page(self, *args, **kwargs):
        return EnhancedPage(*args, **kwargs)

    def get_page_lengths(self):
        if self.per_page not in self.default_page_lengths:
            return sorted([*self.default_page_lengths, self.per_page])
        return self.default_page_lengths


class EnhancedPage(Page):

    def smart_pages(self):
        if self.paginator.num_pages <= 5:
            return self.paginator.page_range

        n = self.number
        pages_wanted = [1, n - 2, n - 1, n, n + 1, n + 2, self.paginator.num_pages]
        page_list = sorted(set(self.paginator.page_range).intersection(pages_wanted))

        skip_pages = [x[1] for x in zip(page_list[:-1], page_list[1:]) if (x[1] - x[0] != 1)]
        for i in skip_pages:
            page_list.insert(page_list.index(i), False)

        return page_list
