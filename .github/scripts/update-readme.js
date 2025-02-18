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

    await new Promise(resolve => setTimeout(resolve, 1000)); // 1-second delay

    const username = 'ripred';
    const reposPerPage = 100;
    let allRepos = [];
    let page = 1;

    try {
        // Fetch all repositories
        while (true) {
            // ATTEMPT 10a: Try octokit.repos.getForUser 
            const reposResponse = await octokit.repos.getForUser({  // CHANGED: octokit.repos.getForUser
                username: username,
                per_page: reposPerPage, // May not be valid in 'getForUser' - check docs if this works and adjust if needed
                page: page,         // May not be valid in 'getForUser' - check docs if this works and adjust if needed
                sort: 'pushed',      // May not be valid in 'getForUser' - check docs if this works and adjust if needed
                direction: 'desc', // May not be valid in 'getForUser' - check docs if this works and adjust if needed
            });

            if (reposResponse.data.length === 0) {
                break;
            }
            allRepos = allRepos.concat(reposResponse.data);
            page++;
        }

        // ... (rest of the script - no changes) ...

    } catch (error) {
        console.error("Error fetching repository stats:", error);
    }
}

main();
