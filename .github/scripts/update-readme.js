const { Octokit } = require("octokit"); // Corrected require statement
const fs = require('fs');

async function main() {
  const octokit = new Octokit({
    auth: process.env.GITHUB_TOKEN,
  });

  const username = 'ripred'; // Replace with your GitHub username
  const reposPerPage = 100; // Adjust as needed, up to 100
  let allRepos = [];
  let page = 1;

  try {
    // Fetch all repositories (paginated API calls)
    while (true) {
      const reposResponse = await octokit.rest.repos.listForUser({
        username: username,
        per_page: reposPerPage,
        page: page,
        sort: 'pushed', // Sort by pushed date (optional)
        direction: 'desc', // Descending order (optional)
      });

      if (reposResponse.data.length === 0) {
        break; // No more repos on this page
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
        const trafficResponse = await octokit.rest.repos.getTrafficViews({
          owner: username,
          repo: repo.name,
        });

        repoStats.push({
          name: repo.name,
          stars: repo.stargazers_count,
          forks: repo.forks_count,
          views: trafficResponse.data.count || 0, // Use 0 if views are not available
          cloneViews: trafficResponse.data.clones?.count || 0 // Example, you could also track clones
        });
      } catch (error) {
        console.error(`Error fetching traffic for ${repo.name}: ${error.message}`);
        repoStats.push({ // Still include basic info even if traffic fails
          name: repo.name,
          stars: repo.stargazers_count,
          forks: repo.forks_count,
          views: 0,
          cloneViews: 0
        });
      }
    }

    // Sort repositories by views, stars, and forks
    const sortedByViews = [...repoStats].sort((a, b) => b.views - a.views);
    const sortedByStars = [...repoStats].sort((a, b) => b.stars - a.stars);
    const sortedByForks = [...repoStats].sort((a, b) => b.forks - b.forks);

    // --- Generate README Content ---
    let readmeContent = fs.readFileSync('README.md', 'utf-8'); // Read existing README
    const statsStartIndex = readmeContent.indexOf('');
    const statsEndIndex = readmeContent.indexOf('');

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
        readmeContent = readmeContent.substring(0, statsStartIndex) + newStatsContent + readmeContent.substring(statsEndIndex + ''.length);
    } else {
        readmeContent += newStatsContent; // Append if markers not found (consider adding markers initially to your README)
    }


    fs.writeFileSync('README.md', readmeContent);
    console.log("README.md updated with repository stats!");

  } catch (error) {
    console.error("Error fetching repository stats:", error);
  }
}

main();
