const fs = require('fs');

async function main() {
    let Octokit;

    try {
        const octokitModule = await import('octokit');
        console.log("Octokit Module:", octokitModule);
        Octokit = octokitModule.Octokit;
    } catch (err) {
        console.error("Error importing Octokit:", err);
        return;
    }

    const octokit = new Octokit({
        auth: process.env.GITHUB_TOKEN,
    });

    await new Promise(resolve => setTimeout(resolve, 50)); // ADDED: Short delay after Octokit instantiation

    const username = 'ripred';
    const reposPerPage = 100;
    let allRepos = [];
    let page = 1;

    try {
        // Fetch all repositories
        while (true) {
            const reposResponse = await octokit.repos.listForUser({
                username: username,
                per_page: reposPerPage,
                page: page,
                sort: 'pushed',
                direction: 'desc',
            });

            if (reposResponse.data.length === 0) {
                break;
            }
            allRepos = allRepos.concat(reposResponse.data);
            page++;
        }

        if (allRepos.length === 0) {
            console.log("No repositories found for user:", username);
            return;
        }

        // Fetch traffic data and prepare stats
        const repoStats = [];
        for (const repo of allRepos) {
            try {
                const trafficResponse = await octokit.repos.getTrafficViews({
                    owner: username,
                    repo: repo.name,
                });

                repoStats.push({
                    name: repo.name,
                    stars: repo.stargazers_count,
                    forks: repo.forks_count,
                    views: trafficResponse.data.count || 0,
                    cloneViews: trafficResponse.data.clones?.count || 0
                });
            } catch (error) {
                console.error(`Error fetching traffic for ${repo.name}: ${error.message}`);
                repoStats.push({
                    name: repo.name,
                    stars: repo.stargazers_count,
                    forks: repo.forks_count,
                    views: 0,
                    cloneViews: 0
                });
            }
        }

        // Sort repositories and generate README content (no changes here)
        const sortedByViews = [...repoStats].sort((a, b) => b.views - a.views);
        const sortedByStars = [...repoStats].sort((a, b) => b.stars - a.stars);
        const sortedByForks = [...repoStats].sort((a, b) => b.forks - b.forks);


        let readmeContent = fs.readFileSync('README.md', 'utf-8');
        const statsStartIndex = readmeContent.indexOf('## 📊 Repository Stats');
        const statsEndIndex = readmeContent.indexOf('##', statsStartIndex + 1);


        let newStatsContent = `
## 📊 Repository Stats

Here's a look at some stats for my repositories, automatically updated daily:

### 🚀 Most Viewed Repositories

These are the repositories with the most views in the last 14 days:

${sortedByViews.slice(0, 5).map(repo => `- **[${repo.name}](https://github.com/${username}/${repo.name})**: ${repo.views} views`).join('\n')}

### ⭐ Most Starred Repositories

My most starred repositories:

${sortedByStars.slice(0, 5).map(repo => `- **[${repo.name}](https://github.com/${username}/${repo.name})**: ${repo.stars} stars`).join('\n')}

### 🍴 Most Forked Repositories

Repositories that have been forked the most:

${sortedByForks.slice(0, 5).map(repo => `- **[${repo.name}](https://github.com/${username}/${repo.name})**: ${repo.forks} forks`).join('\n')}

`;

        if (statsStartIndex !== -1 && statsEndIndex !== -1) {
            readmeContent = readmeContent.substring(0, statsStartIndex) + newStatsContent + readmeContent.substring(statsEndIndex);
        } else {
            readmeContent += newStatsContent;
        }


        fs.writeFileSync('README.md', readmeContent);
        console.log("README.md updated with repository stats!");

    } catch (error) {
        console.error("Error fetching repository stats:", error);
    }
}

main();
