const { test } = require('node:test');
const assert = require('node:assert/strict');
const autoPR = require('../.github/scripts/auto-pr.cjs');

function fixture(options = {}) {
  const calls = { lists: [], comparisons: [], creates: [], info: [], failures: [] };
  let reads = 0;
  const context = {
    eventName: 'push', payload: {}, ref: options.ref || 'refs/heads/feat/example',
    repo: { owner: 'owner', repo: 'repo' },
  };
  const list = () => {};
  const github = {
    paginate: async (method, args) => {
      assert.equal(method, list);
      calls.lists.push(args);
      if (options.listError) throw { status: options.listError };
      return (reads++ ? options.secondExisting : options.existing) || [];
    },
    rest: {
      pulls: {
        list,
        create: async (args) => {
          calls.creates.push(args);
          if (options.createError) throw { status: options.createError };
          return { data: { html_url: 'https://github.com/owner/repo/pull/1' } };
        },
      },
      repos: {
        compareCommits: async (args) => {
          calls.comparisons.push(args);
          if (options.compareError) throw { status: options.compareError };
          return { data: { ahead_by: options.ahead ?? 1, files: options.files ?? [{ filename: 'code.py' }] } };
        },
      },
    },
  };
  const core = { info: (msg) => calls.info.push(msg), setFailed: (msg) => calls.failures.push(msg) };
  return { calls, context, github, core };
}

for (const ref of ['refs/heads/feat/example', 'refs/heads/fix/axes', 'refs/heads/feat/nested/topic']) {
  test(`creates one PR targeting main: ${ref}`, async () => {
    const f = fixture({ ref });
    await autoPR(f);
    assert.equal(f.calls.creates.length, 1);
    assert.equal(f.calls.creates[0].base, 'main');
    assert.equal(f.calls.creates[0].head, ref.slice('refs/heads/'.length));
    assert.equal(f.calls.lists[0].head, `owner:${f.calls.creates[0].head}`);
    assert.equal(f.calls.lists[0].state, 'open');
    assert.equal(f.calls.failures.length, 0);
  });
}

test('leaves an existing open PR unchanged, including one with another base', async () => {
  const f = fixture({ existing: [{ html_url: 'existing', base: { ref: 'other' } }] });
  await autoPR(f);
  assert.equal(f.calls.creates.length, 0);
  assert.equal(f.calls.comparisons.length, 0);
  assert.match(f.calls.info[0], /existing/);
});

for (const ref of ['refs/heads/main', 'refs/heads/feature/foo', 'refs/tags/feat/example']) {
  test(`ignores unrelated ref ${ref}`, async () => {
    const f = fixture({ ref });
    await autoPR(f);
    assert.equal(f.calls.lists.length, 0);
  });
}

test('ignores branch deletion', async () => {
  const f = fixture(); f.context.payload.deleted = true;
  await autoPR(f);
  assert.equal(f.calls.lists.length, 0);
});

test('ignores non-push events', async () => {
  const f = fixture(); f.context.eventName = 'pull_request';
  await autoPR(f);
  assert.equal(f.calls.lists.length, 0);
});

for (const options of [{ ahead: 0 }, { files: [] }]) {
  test(`skips branches without a diff ${JSON.stringify(options)}`, async () => {
    const f = fixture(options);
    await autoPR(f);
    assert.equal(f.calls.creates.length, 0);
    assert.equal(f.calls.failures.length, 0);
  });
}

test('concurrent PR creation is handled without a second create', async () => {
  const f = fixture({ createError: 422, secondExisting: [{ html_url: 'racing-pr' }] });
  await autoPR(f);
  assert.equal(f.calls.creates.length, 1);
  assert.equal(f.calls.lists.length, 2);
  assert.equal(f.calls.failures.length, 0);
});

for (const options of [{ createError: 403 }, { listError: 403 }, { createError: 422 }, { compareError: 404 }]) {
  test(`reports failure with manual link, never retries creation: ${JSON.stringify(options)}`, async () => {
    const f = fixture(options);
    await autoPR(f);
    assert.ok(f.calls.creates.length <= 1);
    assert.equal(f.calls.failures.length, 1);
    assert.match(f.calls.failures[0], /compare\/main\.\.\.feat%2Fexample\?expand=1/);
    if (options.createError === 403 || options.listError === 403) {
      assert.match(f.calls.failures[0], /Allow GitHub Actions/);
    }
  });
}
