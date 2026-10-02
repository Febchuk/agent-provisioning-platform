# On-site Build: An Agent Platform

Brainbase builds the primitives teams use to run internal agents at scale. Today you'll build a small version of that idea. The brief is deliberately open. We care more about the decisions you make than about how much you ship.

## The problem

Build a platform where a user can:

- **Create** agents. The platform is general-purpose: your users decide what their agents do, not you. These agents should be able to run and write code, edit/view files and more\!  
- **Deploy** them, so they can be used by someone (or something) other than the person who made them.  
- **Improve** them. Agents on your platform should get better at their job over time.  
- (optional) \- agents run using an **open-source model** that has been deployed on GPUs.

What we expect at the end

- Working software, demoed live. Rough edges are fine; a slide deck alone is not.  
- At least one agent built on your platform, and a demo of how it gets better.  
- A \~20 minute walkthrough followed by Q\&A: what you built, what you cut, why, and what you'd do with another week.

## Ground rules

- You have 5 hours. Any language, any stack, local or hosted.  
- AI coding tools are encouraged. We use them all day.  
- We'll give you LLM API keys and/or coding subscriptions if needed.  
- Open-source libraries and hosted services are fine. Tell us what you wrote and what you pulled in.  
- Ask us questions whenever you want. Treat us as teammates.  
- Go deep rather than wide. One part that works well beats five that half work.

## What we look at (optional section)

- **Product judgment:** who is your user, what did you prioritize, what did you leave out  
- **UX:** can someone who has never seen it create an agent and make it better  
- **Engineering:** architecture, trade-offs you made for time, what would break first at scale  
- **Ambiguity:** how you turned a vague brief into a concrete plan  
- **Communication:** can you defend your decisions and be honest about the weak spots

## Clarifying Questions

- **Who are the typical agent builders?**  
  - An engineer at a 50-person company who wants to build a small internal agent once and hand it to non-technical teammates  
    - Create needs to be quick  
    - Deploy means someone else is using it  
    - Improve means teammates’ feedback makes it better? Or LLM as a judge?  
- What does the agent def look like?  
  - System Prompt  
  - Model  
  - Tools  
  - Skills?  
    - Md files?  
      	  
1. *"Who creates agents on Brainbase today, engineers or ops/business people? I'm assuming an engineer builds the agent and non-technical teammates use it, which changes how much config I expose at creation versus hide."*  
   *Good assumption*  
2. *"When an agent is 'deployed' for your customers, who or what calls it most often: a human in a chat UI, another service over an API, or a trigger like a cron or webhook? I'm planning API-first with a share page on top."*  
   *Lets go chat ui first, api second*  
3. *"When you say agents should improve over time, is the signal human feedback, objective outcomes like tests passing, or both? I'm planning to turn feedback into eval cases so every improvement is measured against them, not just trusted."*  
   *Both human feedback and specific cases*  
4. *"Should improvements go live automatically, or should a human approve them? I'm leaning toward the system proposing a new version with an eval diff and the owner promoting it, because an agent silently rewriting itself in production is how you lose trust."*  
   *I like proposing a diff*  
5. *"Which matters more to your customers: an improvement that wins on average, or one that never regresses on cases that used to pass? I'm planning to block promotion on any regression."*  
   *Maybe we can have users set a threshold, and since we are proposing diffs and users accept anyway, then they can hoose to accept a diff whenere average increases, but regression along one of the axes.*  
6. *"How much isolation do you expect for code execution in a 5-hour build? I'm planning a container per run and will be upfront that it's not a real security boundary. Is that the right line, or is sandboxing something you want to see go deep?"*  
   *Good assumption*  
7. *"Is the open-source GPU model there to test infra skill, or to see that the design isn't locked to one provider? If it's the second, I'll keep the model client endpoint-swappable and spend the time on the improvement loop instead."*  
   *Swappable ok to start, can be the last thing we explore*

   

