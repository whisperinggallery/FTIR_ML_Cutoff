// Called by actions/github-script; API client/token are injected by Actions.
module.exports = async function autoPR({ github, context, core }) {
  if (context.eventName !== 'push' || context.payload.deleted ||
      !/^refs\/heads\/(feat|fix)\/.+/.test(context.ref)) {
    core.info('Not a supported feature/fix branch push; skipped.');
    return;
  }
  const { owner, repo } = context.repo;
  const branch = context.ref.slice('refs/heads/'.length);
  const head = `${owner}:${branch}`;
  const compareURL = `https://github.com/${owner}/${repo}/compare/main...${encodeURIComponent(branch)}?expand=1`;
  const findOpen = () => github.paginate(github.rest.pulls.list, {
    owner, repo, state: 'open', head, per_page: 100,
  });
  const reportExisting = (pulls) => core.info(`Open PR already exists; unchanged: ${pulls[0].html_url}`);
  try {
    const existing = await findOpen();
    if (existing.length) {
      reportExisting(existing);
      return;
    }
    const comparison = await github.rest.repos.compareCommits({ owner, repo, base: 'main', head: branch });
    if (comparison.data.ahead_by === 0 || comparison.data.files?.length === 0) {
      core.info('No changes ahead of main; no PR needed.');
      return;
    }
    try {
      const { data } = await github.rest.pulls.create({
        owner, repo, base: 'main', head: branch,
        title: `자동 PR: ${branch}`,
        body: [
          `\`${branch}\` 브랜치 Push로 자동 생성한 Pull Request입니다.`,
          '',
          '변경 내용과 별도로 실행되는 코드 테스트 결과를 검토한 뒤 사람이 병합해 주세요.',
          '이 워크플로는 자동 병합을 수행하지 않습니다.',
        ].join('\n'),
      });
      core.info(`Created PR: ${data.html_url}`);
    } catch (error) {
      // Another actor may have created a PR after our initial read. Never retry creation.
      if (error.status === 422) {
        const existing = await findOpen();
        if (existing.length) {
          reportExisting(existing);
          return;
        }
      }
      throw error;
    }
  } catch (error) {
    const detail = error.status === 403
      ? 'Check Settings > Actions > General > Allow GitHub Actions to create and approve pull requests, and organization policy.'
      : 'Check repository access, branch availability, and workflow logs.';
    core.setFailed(`Automatic PR creation failed (HTTP ${error.status || 'unknown'}). ${detail} Create manually: ${compareURL}`);
  }
};
