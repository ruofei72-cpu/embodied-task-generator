# References and scope of reuse

The implementation in this repository is independently written. These projects
informed the design concepts; none is a runtime dependency or bundled source.

| Reference | Concrete idea used here | Not implemented here |
| --- | --- | --- |
| [RLBench task walkthrough](https://github.com/stepjam/RLBench/blob/master/tutorials/simple_task.md) and [pick_up_cup.py](https://github.com/stepjam/RLBench/blob/master/rlbench/tasks/pick_up_cup.py) | Separate language descriptions from variations in object attributes and physical initial states | Robot control, simulation sampling, motion-planned demonstrations and task success sensors |
| [LIBERO task_generation_utils.py](https://github.com/Lifelong-Robot-Learning/LIBERO/blob/master/libero/libero/utils/task_generation_utils.py) | Represent scene, objects, language and goals explicitly; check object compatibility before generating task files | LIBERO environment integration, BDDL execution and policy evaluation |
| [BEHAVIOR task definitions](https://github.com/StanfordVL/BEHAVIOR-1K/blob/main/docs/behavior_components/behavior_tasks.md) | Distinguish objects, initial conditions, goals and concrete task instances | Logical goal evaluation, simulator-based feasibility checks and scene generation |

The public seven-axis configuration is illustrative. Customer-specific dimension
names, inventory, forbidden combinations and quotas must be supplied separately.
No original internal task library or supplier data is included.
