Category Tree component
-----------------------

Files added:
- static/components/category-tree/category-tree.css
- static/components/category-tree/category-tree.js
- templates/components/category_tree_demo.html

Quick usage (Django template):

1. Ensure `collectstatic` serves `static/components/category-tree/*`.
2. In your template:

```django
{% load static %}
<link rel="stylesheet" href="{% static 'components/category-tree/category-tree.css' %}">
<div class="category-tree" data-json='[...]'></div>
<script src="{% static 'components/category-tree/category-tree.js' %}"></script>
```

Notes:
- Use `data-json` with a JSON array of items ({name, meta?, children?}).
- Or render nested <ul><li> markup and include `class="category-tree"` to auto-wire toggles.
- Component is keyboard accessible: Enter/Space toggles; ArrowLeft/ArrowRight collapse/expand.
