Project Idea: 

Parsimony currenlty organizes stack allocation in parallel regions as an array of structs, where each struct corresponds to a SIMD lane. In some instances, a struct of arrays yields improved performance. 

We aim to heuristically determine which pattern is optimal for each region at compile time. 