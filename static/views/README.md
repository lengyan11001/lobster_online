# Online View Fragments

`index.html` still contains the current DOM, so existing views keep working while the page is split gradually.

To move one view out:

1. Create `/static/views/<view>.html`.
2. Put the whole view root in that file, for example:

```html
<div id="content-scheduled-tasks" class="content-block">
  ...
</div>
```

3. Move the view's page logic to `/static/js/views/<view>.js` when it has its own init/bind/load functions.
4. Register it before navigation can open the view:

```js
window.registerLobsterView('scheduled-tasks', {
  html: '/static/views/scheduled-tasks.html',
  scripts: '/static/js/views/scheduled-tasks.js?v=20260528-view-split',
  init: 'initScheduledTasksView'
});
```

If the old `#content-<view>` block still exists in `index.html`, the loader uses that block and does not fetch the fragment.
When `scripts` is set, the loader fetches the HTML first, then loads the script, then the normal navigation init runs.
