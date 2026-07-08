G-01 = 

G-14 = 
planner failed because we get this error =
ValueError(
  "Planner produced invalid semantic EDIT steps: step 2: target 'this.searchValue' (assignment) must be unique in frontend/src/app/search-result/search-result.component.ts, found 2 matches; step 3: could not resolve assignment 'this.trustProductDescription' in frontend/src/app/search-result/search-result.component.ts; step 4: could not resolve variable 'searchValue' in frontend/src/app/search-result/search-result.component.ts"
)

Traceback (most recent call last):
  File "/Users/prageet.mishra/Documents/Intern_Project_Coding_Agent/src/agents/planner/agent.py", line 229, in run_planner
    raise ValueError(
ValueError: Planner produced invalid semantic EDIT steps: step 2: target 'this.searchValue' (assignment) must be unique in frontend/src/app/search-result/search-result.component.ts, found 2 matches; step 3: could not resolve assignment 'this.trustProductDescription' in frontend/src/app/search-result/search-result.component.ts; step 4: could not resolve variable 'searchValue' in frontend/src/app/search-result/search-result.component.ts
