const fs = require('fs');
const Octokit = require('octokit').Octokit; // CHANGED: CommonJS require

async function main() {

    const octokit = new Octokit({
        auth: process.env.GITHUB_TOKEN,
    });

    await new Promise(resolve => setTimeout(resolve, 1000)); // 1-second delay

    const username = 'ripred';
    const repoName = 'ripred'; // CHANGED: Focus on a single repo

    try {
        // ATTEMPT 10b: Get data for a single, known repository (ripred/ripred)
        const repoResponse = await octokit.repos.get({ // CHANGED: octokit.repos.get (for single repo)
            owner: username,
            repo: repoName,
        });

        console.log("Repository Data:", repoResponse.data); // Log the single repo data

        const repoStats = [{ // Create stats for this single repo
            name: repoResponse.data.name,
            stars: repoResponse.data.stargazers_count,
            forks: repoResponse.data.forks_count,
            views: 0, // Traffic data might be harder with older versions, skip for now
            cloneViews: 0
        }];


        // Sort repositories and generate README content (no changes here) - will now be based on single repo stats
        const sortedByViews = [...repoStats].sort((a, b) => b.views - a.views); // Sort might be less relevant now, but keep it
        const sortedByStars = [...repoStats].sort((a, b) => b.stars - a.stars);
        const sortedByForks = [...repoStats].sort((a, b) => b.forks - b.forks);


        let readmeContent = fs.readFileSync('README.md', 'utf-8');
        const statsStartIndex = readmeContent.indexOf('## 📊 Repository Stats');
        const statsEndIndex = readmeContent.indexOf('##', statsStartIndex + 1);


        let newStatsContent = `
## 📊 Repository Stats (Attempt 10b - Single Repo Test)

Stats for the **${repoName}** repository, automatically updated daily:

### ⭐ Stars: ${repoStats[0].stars}
### 🍴 Forks: ${repoStats[0].forks}
`; // Simplified stats output for single repo test


        if (statsStartIndex !== -1 && statsEndIndex !== -1) {
            readmeContent = readmeContent.substring(0, statsStartIndex) + newStatsContent + readmeContent.substring(statsEndIndex);
        } else {
            readmeContent += newStatsContent;
        }


        fs.writeFileSync('README.md', readmeContent);
        console.log("README.md updated with repository stats (Attempt 10b - Single Repo)!");

    } catch (error) {
        console.error("Error fetching repository stats (Attempt 10b - Single Repo):", error);
    }
}

main();
