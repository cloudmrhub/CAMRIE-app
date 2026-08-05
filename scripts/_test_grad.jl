using KomaMRI
g = Grad(1.0, 1.0)
println("fields: ", fieldnames(typeof(g)))
println("first: ", g.first, " last: ", g.last)
# Test keyword constructor
g2 = Grad(A=g.A * 0.5, T=g.T, rise=g.rise, fall=g.fall, delay=g.delay, first=g.first, last=g.last)
println("keyword constructor OK: A=", g2.A)
# Test copy constructor with Grad()
g3 = Grad(; A=g.A * 0.5, T=g.T, rise=g.rise, fall=g.fall, delay=g.delay, first=g.first, last=g.last)
println("Grad kw OK: ", g3.A)
