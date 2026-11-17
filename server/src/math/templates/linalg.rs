//! `templates/linalg.py`: vectors, dot products, matrix products, determinants, inverses and 2 × 2 eigenvalues.

use crate::math::frac::{floordiv, pymod, F};
use crate::math::problems::{line, note, step, template, wrong, Spec, Step, Template};
use crate::math::pyrandom::Random;

pub static TEMPLATES: &[Template] = &[
    template("linalg.vectors/combination", 20, combination),
    template("linalg.norm/length", 30, length),
    template("linalg.norm/distance", 40, distance),
    template("linalg.dot/product", 20, dot),
    template("linalg.orthogonal/solve-k", 40, orthogonal),
    template("linalg.matvec/entry", 30, matvec),
    template("linalg.matmul/entry-2x2", 30, matmul_square),
    template("linalg.matmul/entry-2x3", 40, matmul_rect),
    template("linalg.det2/ad-bc", 15, det2),
    template("linalg.det3/cofactor", 90, det3),
    template("linalg.inverse2/entry", 45, inverse2),
    template("linalg.inverse3/entry", 150, inverse3),
    template("linalg.eigen2/char-poly", 90, eigen2),
];

const ORDINAL: [&str; 4] = ["first", "second", "third", "fourth"];
const CAPITAL: [&str; 4] = ["First", "Second", "Third", "Fourth"];
/// Integer-length directions: Pythagorean triples (2-D) and quadruples (3-D), length last.
const TRIPLES: &[&[i64]] = &[&[3, 4, 5], &[5, 12, 13], &[8, 15, 17], &[7, 24, 25], &[6, 8, 10], &[9, 12, 15], &[12, 16, 20]];
const QUADRUPLES: &[&[i64]] = &[&[1, 2, 2, 3], &[2, 3, 6, 7], &[1, 4, 8, 9], &[4, 4, 7, 9], &[2, 6, 9, 11], &[6, 6, 7, 11],
                                &[2, 4, 4, 6], &[2, 10, 11, 15], &[8, 9, 12, 17]];

type Mat = Vec<Vec<i64>>;

/// `_vec` over already-printed entries.
fn vec_s(v: &[String]) -> String {
    format!("\\begin{{pmatrix}} {} \\end{{pmatrix}}", v.join(" \\\\ "))
}

/// `_vec`.
fn vect(v: &[i64]) -> String {
    vec_s(&v.iter().map(i64::to_string).collect::<Vec<_>>())
}

/// `_mat`.
fn mat(rows: &Mat) -> String {
    let rows: Vec<String> = rows.iter().map(|row| row.iter().map(i64::to_string).collect::<Vec<_>>().join(" & ")).collect();
    format!("\\begin{{pmatrix}} {} \\end{{pmatrix}}", rows.join(" \\\\ "))
}

/// `_p`: a factor in a product: negatives get parentheses.
fn p(x: i64) -> String {
    if x < 0 { format!("({x})") } else { x.to_string() }
}

/// `_sum`: integers as a sum with signs folded in.
fn sum_tex(values: &[i64]) -> String {
    let mut out = values[0].to_string();
    for &x in &values[1..] {
        out += &if x < 0 { format!(" - {}", -x) } else { format!(" + {x}") };
    }
    out
}

/// `_ints`.
fn ints(r: &mut Random, n: usize, lo: i64, hi: i64) -> Vec<i64> {
    (0..n).map(|_| r.randint(lo, hi)).collect()
}

/// `_nonzero`.
fn nonzero(r: &mut Random, lo: i64, hi: i64) -> i64 {
    r.choice(&(lo..=hi).filter(|x| *x != 0).collect::<Vec<_>>())
}

/// `_matrix`.
fn matrix(r: &mut Random, rows: usize, cols: usize, lo: i64, hi: i64) -> Mat {
    (0..rows).map(|_| ints(r, cols, lo, hi)).collect()
}

/// `_term`: `coefficient·name` as the first or a later term of a sum.
fn term(coefficient: i64, name: &str, first: bool) -> String {
    let magnitude = if coefficient.abs() == 1 { String::new() } else { coefficient.abs().to_string() };
    let sign = if first { if coefficient < 0 { "-" } else { "" } } else if coefficient < 0 { " - " } else { " + " };
    format!("{sign}{magnitude}{name}")
}

/// `_det2`.
fn d2(rows: &Mat) -> i64 {
    rows[0][0] * rows[1][1] - rows[0][1] * rows[1][0]
}

/// `_det2_tex`.
fn d2_tex(rows: &Mat) -> String {
    format!("{} \\cdot {} - {} \\cdot {}", p(rows[0][0]), p(rows[1][1]), p(rows[0][1]), p(rows[1][0]))
}

/// `_minor`: `rows` without row `i` and column `j`.
fn minor(rows: &Mat, i: usize, j: usize) -> Mat {
    rows.iter().enumerate().filter(|(k, _)| *k != i)
        .map(|(_, row)| row.iter().enumerate().filter(|(c, _)| *c != j).map(|(_, x)| *x).collect()).collect()
}

/// `_det3`.
fn d3(rows: &Mat) -> i64 {
    (0..3).map(|j| if j % 2 == 0 { 1 } else { -1 } * rows[0][j] * d2(&minor(rows, 0, j))).sum()
}

/// `_expand_tex`: row-1 cofactor expansion with the minors' values.
fn expand_tex(rows: &Mat) -> String {
    let m: Vec<i64> = (0..3).map(|j| d2(&minor(rows, 0, j))).collect();
    format!("{}({}) - {}({}) + {}({})", p(rows[0][0]), m[0], p(rows[0][1]), m[1], p(rows[0][2]), m[2])
}

/// `_product_steps`: one step per matching product, then their sum.
fn product_steps(row: &[i64], column: &[i64], label: &str) -> Vec<Step> {
    let products: Vec<i64> = row.iter().zip(column).map(|(x, y)| x * y).collect();
    let mut steps: Vec<Step> = row.iter().zip(column).enumerate()
        .map(|(k, (&x, &y))| step(format!("{} pair", CAPITAL[k]), format!("{} \\cdot {}", p(x), p(y)), x * y)).collect();
    steps.push(step(label, sum_tex(&products), products.iter().sum::<i64>()));
    steps
}

/// `_length`: a random integer-length direction: signed, shuffled components and the length.
fn rand_length(r: &mut Random) -> (Vec<i64>, i64) {
    let base = if r.random() < 0.5 { r.choice(TRIPLES) } else { r.choice(QUADRUPLES) };
    let mut parts: Vec<i64> = base[..base.len() - 1].iter().map(|x| x * r.choice(&[1, -1])).collect();
    r.shuffle(&mut parts);
    (parts, base[base.len() - 1])
}

fn dotp(u: &[i64], v: &[i64]) -> i64 {
    u.iter().zip(v).map(|(x, y)| x * y).sum()
}

fn combination(r: &mut Random) -> Spec {
    let n = r.choice(&[2usize, 3]);
    let (u, v) = (ints(r, n, -6, 6), ints(r, n, -6, 6));
    let (a, b) = (nonzero(r, -4, 4), nonzero(r, -4, 4));
    let i = r.randrange(0, n as i64) as usize;
    let answer = a * u[i] + b * v[i];
    let combo = term(a, "\\mathbf u", true) + &term(b, "\\mathbf v", false);
    let k = i + 1;
    Spec::new(format!("Let $\\mathbf u = {}$ and $\\mathbf v = {}$. What is the {} component of ${combo}$?", vect(&u), vect(&v), ORDINAL[i]), answer, vec![
        line("Componentwise: only the matching components combine", format!("({combo})_{k} = {a}u_{k} + {}v_{k}", p(b))),
        step(format!("Scale $u_{k}$"), format!("{a} \\cdot {}", p(u[i])), a * u[i]),
        step(format!("Scale $v_{k}$"), format!("{b} \\cdot {}", p(v[i])), b * v[i]),
        step("Add", sum_tex(&[a * u[i], b * v[i]]), answer),
    ]).mistakes(vec![
        wrong(a * u[i] + v[i], format!("Scale both vectors: multiply $v_{k}$ by {b} too.")),
        wrong(u[i] + v[i], "Multiply each vector by its scalar before adding."),
        wrong(a * u[i] - b * v[i], format!("Check the sign of the second term: ${b} \\cdot {} = {}$.", p(v[i]), b * v[i])),
    ])
}

fn squares_tex(u: &[i64]) -> String {
    u.iter().map(|x| format!("{}^2", p(*x))).collect::<Vec<_>>().join(" + ")
}

fn length(r: &mut Random) -> Spec {
    let (u, answer) = rand_length(r);
    let squares: i64 = u.iter().map(|x| x * x).sum();
    Spec::new(format!("What is the length $\\lVert \\mathbf u \\rVert$ of $\\mathbf u = {}$?", vect(&u)), answer, vec![
        step("Sum of squares", squares_tex(&u), squares),
        step("Square root", format!("\\sqrt{{{squares}}}"), answer),
    ]).mistakes(vec![
        wrong(squares, "That's $\\lVert \\mathbf u \\rVert^2$; take the square root."),
        wrong(u.iter().map(|x| x.abs()).sum::<i64>(), "Length isn't the sum of the components: square them, add, then take the square root."),
    ])
}

fn distance(r: &mut Random) -> Spec {
    let (d, answer) = rand_length(r);
    let v = ints(r, d.len(), -5, 5);
    let u: Vec<i64> = v.iter().zip(&d).map(|(x, y)| x + y).collect();
    let squares: i64 = d.iter().map(|x| x * x).sum();
    Spec::new(format!("What is the distance $\\lVert \\mathbf u - \\mathbf v \\rVert$ between $\\mathbf u = {}$ and $\\mathbf v = {}$?", vect(&u), vect(&v)), answer, vec![
        line("Subtract componentwise", format!("\\mathbf u - \\mathbf v = {}", vect(&d))),
        step("Sum of squares", squares_tex(&d), squares),
        step("Square root", format!("\\sqrt{{{squares}}}"), answer),
    ]).mistakes(vec![
        wrong(squares, "That's the squared distance; take the square root."),
        wrong(d.iter().map(|x| x.abs()).sum::<i64>(), "Distance isn't the sum of the differences: square them, add, then take the square root."),
    ])
}

fn dot(r: &mut Random) -> Spec {
    let n = r.choice(&[2usize, 3, 4]);
    let (u, v) = (ints(r, n, -6, 7), ints(r, n, -6, 7));
    let answer = dotp(&u, &v);
    Spec::new(format!("What is $\\mathbf u \\cdot \\mathbf v$ for $\\mathbf u = {}$ and $\\mathbf v = {}$?", vect(&u), vect(&v)), answer,
              product_steps(&u, &v, "Add the products")).mistakes(vec![
        wrong(u.iter().zip(&v).map(|(x, y)| x + y).sum::<i64>(), "Multiply matching components, then add the products."),
        wrong(u.iter().zip(&v).map(|(x, y)| (x * y).abs()).sum::<i64>(), "Watch the signs: a negative times a positive is negative."),
    ])
}

fn orthogonal(r: &mut Random) -> Spec {
    let n = r.choice(&[2usize, 3]);
    let (u, j, v, known) = loop {
        let u: Vec<i64> = (0..n).map(|_| nonzero(r, -6, 6)).collect();
        let j = r.randrange(0, n as i64) as usize;
        let v: Vec<Option<i64>> = (0..n).map(|k| if k == j { None } else { Some(r.randint(-6, 6)) }).collect();
        let known: i64 = (0..n).filter(|k| *k != j).map(|k| u[k] * v[k].unwrap()).sum();
        if known != 0 {
            break (u, j, v, known);
        }
    };
    let answer = F::new(-known, u[j]);
    let pairs = (0..n).filter(|k| *k != j).map(|k| format!("{} \\cdot {}", p(u[k]), p(v[k].unwrap()))).collect::<Vec<_>>().join(" + ");
    let vs: Vec<String> = v.iter().map(|x| x.map_or("k".to_string(), |x| x.to_string())).collect();
    Spec::new(format!("For which $k$ are $\\mathbf u = {}$ and $\\mathbf v = {}$ orthogonal?", vect(&u), vec_s(&vs)), answer, vec![
        line("Orthogonal means", "\\mathbf u \\cdot \\mathbf v = 0"),
        step("The known products", pairs, known),
        step("Solve", format!("{known}{} = 0 \\;\\Rightarrow\\; k", term(u[j], "k", false)), answer),
    ]).kind("fraction").mistakes(vec![
        wrong(F::new(known, u[j]), "Moving the known terms across the $=$ flips their sign."),
        wrong(F::new(-u[j], known), format!("Divide by $k$'s coefficient ${}$, not into it.", u[j])),
        wrong(F::from(-known), format!("Divide by $k$'s coefficient ${}$.", u[j])),
    ])
}

fn matvec(r: &mut Random) -> Spec {
    let (m, n) = (r.choice(&[2usize, 3]), r.choice(&[2usize, 3]));
    let (a, v) = (matrix(r, m, n, -5, 6), ints(r, n, -4, 5));
    let i = r.randrange(0, m as i64) as usize;
    let answer = dotp(&a[i], &v);
    let mut mistakes = vec![wrong(a[i].iter().zip(&v).map(|(x, y)| x + y).sum::<i64>(), "Multiply matching entries, then add the products.")];
    if m == n {
        mistakes.insert(0, wrong((0..n).map(|k| a[k][i] * v[k]).sum::<i64>(), format!("Use row {} of $A$, not column {}.", i + 1, i + 1)));
    }
    let mut steps = vec![note(format!("Entry {} is row {} of $A$ dotted with $\\mathbf v$", i + 1, i + 1))];
    steps.extend(product_steps(&a[i], &v, "Add the products"));
    Spec::new(format!("Let $A = {}$ and $\\mathbf v = {}$. What is entry {} of $A\\mathbf v$?", mat(&a), vect(&v), i + 1), answer, steps).mistakes(mistakes)
}

/// `_matmul`.
fn matmul(r: &mut Random, inner: usize) -> Spec {
    let (a, b) = (matrix(r, 2, inner, -5, 6), matrix(r, inner, 2, -5, 6));
    let (i, j) = (r.randrange(0, 2) as usize, r.randrange(0, 2) as usize);
    let column: Vec<i64> = b.iter().map(|row| row[j]).collect();
    let answer = dotp(&a[i], &column);
    let swapped: i64 = (0..2).map(|k| b[i][k] * a[k][j]).sum();
    let transposed: i64 = a[j].iter().zip(&b).map(|(x, row)| x * row[i]).sum();
    let mut mistakes = vec![
        wrong(swapped, "Order matters: $AB$ takes rows of $A$ and columns of $B$, not the other way round."),
        wrong(transposed, format!("That's the entry in row {}, column {}: the row comes first.", j + 1, i + 1)),
    ];
    if inner == 2 {
        mistakes.insert(0, wrong(a[i][j] * b[i][j], format!("Matrix products aren't entrywise: use row {} of $A$ and column {} of $B$.", i + 1, j + 1)));
    }
    let mut steps = vec![note(format!("Row {} of $A$ dotted with column {} of $B$", i + 1, j + 1))];
    steps.extend(product_steps(&a[i], &column, "Add the products"));
    Spec::new(format!("Let $A = {}$ and $B = {}$. What is the entry in row {}, column {} of $AB$?", mat(&a), mat(&b), i + 1, j + 1), answer, steps)
        .mistakes(mistakes)
}

fn matmul_square(r: &mut Random) -> Spec {
    matmul(r, 2)
}

fn matmul_rect(r: &mut Random) -> Spec {
    matmul(r, 3)
}

fn det2(r: &mut Random) -> Spec {
    let a = matrix(r, 2, 2, -6, 9);
    let (pp, q, s, t) = (a[0][0], a[0][1], a[1][0], a[1][1]);
    let answer = d2(&a);
    Spec::new(format!("What is $\\det {}$?", mat(&a)), answer, vec![
        step("Main diagonal", format!("{} \\cdot {}", p(pp), p(t)), pp * t),
        step("Other diagonal", format!("{} \\cdot {}", p(q), p(s)), q * s),
        step("Subtract", format!("{} - {}", pp * t, p(q * s)), answer),
    ]).mistakes(vec![
        wrong(pp * t + q * s, "Subtract the other diagonal: $ad - bc$."),
        wrong(q * s - pp * t, "Main diagonal minus the other diagonal, not the reverse."),
        wrong(pp * q - s * t, "Multiply along the diagonals, not across the rows."),
    ])
}

fn det3(r: &mut Random) -> Spec {
    let a = matrix(r, 3, 3, -4, 5);
    let (pp, q, s, e, f, h, i) = (a[0][0], a[0][1], a[0][2], a[1][1], a[1][2], a[2][1], a[2][2]);
    let minors: Vec<i64> = (0..3).map(|j| d2(&minor(&a, 0, j))).collect();
    let answer = d3(&a);
    let forward = pp * e * i + q * f * a[2][0] + s * a[1][0] * h;
    let mut steps: Vec<Step> = (0..3).map(|j| step(format!("Minor $M_{{1{}}}$: delete row 1 and column {}", j + 1, j + 1), d2_tex(&minor(&a, 0, j)), minors[j])).collect();
    steps.push(step("Expand along row 1 with signs $+\\,-\\,+$", expand_tex(&a), answer));
    Spec::new(format!("What is $\\det {}$?", mat(&a)), answer, steps).mistakes(vec![
        wrong(pp * minors[0] + q * minors[1] + s * minors[2], "Cofactor signs alternate $+\\,-\\,+$: subtract the middle term."),
        wrong(forward, "Sarrus' rule also subtracts the three products along the anti-diagonals."),
        wrong(pp * e * i, "Only a triangular matrix's determinant is the product of its diagonal."),
    ])
}

fn inverse2(r: &mut Random) -> Spec {
    let (a, det) = loop {
        let a = matrix(r, 2, 2, -4, 6);
        let det = d2(&a);
        if det != 0 {
            break (a, det);
        }
    };
    let (pp, q, s, t) = (a[0][0], a[0][1], a[1][0], a[1][1]);
    let adj: Mat = vec![vec![t, -q], vec![-s, pp]];
    let (i, j) = (r.randrange(0, 2) as usize, r.randrange(0, 2) as usize);
    let answer = F::new(adj[i][j], det);
    let mut mistakes = vec![
        wrong(adj[i][j], "Divide by the determinant: $A^{-1} = \\frac{1}{\\det A}\\,\\operatorname{adj} A$."),
        wrong(F::new(adj[j][i], det), format!("That's the entry in row {}, column {}.", j + 1, i + 1)),
        wrong(F::new(a[i][j], det), if i == j { "Swap the diagonal entries." } else { "Negate the off-diagonal entries." }),
        wrong(if a[i][j] != 0 { F::new(1, a[i][j]) } else { answer }, "A matrix inverse isn't the reciprocal of each entry."),
    ];
    if pp * t + q * s != 0 {
        mistakes.push(wrong(F::new(adj[i][j], pp * t + q * s), "The determinant is $ad - bc$, not $ad + bc$."));
    }
    Spec::new(format!("Let $A = {}$. What is the entry in row {}, column {} of $A^{{-1}}$?", mat(&a), i + 1, j + 1), answer, vec![
        step("Determinant", d2_tex(&a), det),
        line("Adjugate: swap the diagonal, negate the off-diagonal", format!("\\operatorname{{adj}} A = {}", mat(&adj))),
        step(format!("Adjugate entry ({}, {})", i + 1, j + 1), format!("(\\operatorname{{adj}} A)_{{{}{}}}", i + 1, j + 1), adj[i][j]),
        step("Divide by the determinant", format!("\\frac{{{}}}{{{det}}}", adj[i][j]), answer),
    ]).kind("fraction").mistakes(mistakes)
}

fn inverse3(r: &mut Random) -> Spec {
    let (a, det) = loop {
        let a = matrix(r, 3, 3, -3, 4);
        let det = d3(&a);
        if det != 0 && det.abs() <= 30 {
            break (a, det);
        }
    };
    let (i, j) = (r.randrange(0, 3) as usize, r.randrange(0, 3) as usize);
    let mn = minor(&a, j, i);
    let sign = if (i + j) % 2 == 0 { 1 } else { -1 };
    let md = d2(&mn);
    let cofactor = sign * md;
    let other = sign * d2(&minor(&a, i, j));
    let answer = F::new(cofactor, det);
    let plain: i64 = (0..3).map(|k| a[0][k] * d2(&minor(&a, 0, k))).sum();
    let (i1, j1) = (i + 1, j + 1);
    let mut mistakes = vec![
        wrong(F::new(other, det), format!("The adjugate is the transposed cofactor matrix: entry ({i1}, {j1}) of $A^{{-1}}$ uses $C_{{{j1}{i1}}}$.")),
        wrong(F::new(md, det), format!("Apply the cofactor sign $(-1)^{{{j1}+{i1}}}$.")),
        wrong(cofactor, "Divide by the determinant: $A^{-1} = \\frac{1}{\\det A}\\,\\operatorname{adj} A$."),
    ];
    if plain != 0 {
        mistakes.push(wrong(F::new(cofactor, plain), "In the determinant the cofactor signs alternate $+\\,-\\,+$."));
    }
    Spec::new(format!("Let $A = {}$. What is the entry in row {i1}, column {j1} of $A^{{-1}}$?", mat(&a)), answer, vec![
        step("Determinant, expanding along row 1", expand_tex(&a), det),
        note(format!("$(A^{{-1}})_{{{i1}{j1}}} = C_{{{j1}{i1}}} / \\det A$: the adjugate is the transposed cofactor matrix")),
        step(format!("Minor $M_{{{j1}{i1}}}$: delete row {j1} and column {i1}"), d2_tex(&mn), md),
        step(format!("Cofactor, sign $(-1)^{{{j1}+{i1}}}$"), format!("C_{{{j1}{i1}}} = {}({md})", if sign > 0 { '+' } else { '-' }), cofactor),
        step("Divide by the determinant", format!("\\frac{{{cofactor}}}{{{det}}}"), answer),
    ]).kind("fraction").mistakes(mistakes)
}

fn eigen2(r: &mut Random) -> Spec {
    // Pick the eigenvalues, then a matrix with that trace and determinant:
    // a + d = λ₁ + λ₂ and bc = ad − λ₁λ₂ = −(a − λ₁)(a − λ₂).
    let (l1, l2, pp, q, s, t) = loop {
        let ls = r.sample(&(-5..8).collect::<Vec<i64>>(), 2);
        let (l1, l2) = (ls[0], ls[1]);
        let pp = r.choice(&(l1.min(l2) - 3..l1.max(l2) + 4).filter(|x| *x != l1 && *x != l2).collect::<Vec<_>>());
        let product = -(pp - l1) * (pp - l2);
        let qs: Vec<i64> = (1..=product.abs()).filter(|x| pymod(product, *x) == 0).flat_map(|x| [x, -x]).collect();
        let q = r.choice(&qs);
        let (s, t) = (floordiv(product, q), l1 + l2 - pp);
        if pp.abs().max(q.abs()).max(s.abs()).max(t.abs()) <= 12 {
            break (l1, l2, pp, q, s, t);
        }
    };
    let a: Mat = vec![vec![pp, q], vec![s, t]];
    let which = r.choice(&["larger", "smaller"]);
    let (trace, det) = (pp + t, d2(&a));
    let disc = trace * trace - 4 * det;
    let root = (l1 - l2).abs();
    let larger = which == "larger";
    let answer = if larger { l1.max(l2) } else { l1.min(l2) };
    let other = l1 + l2 - answer;
    let sign = if larger { "+" } else { "-" };
    let constant = if det > 0 { format!(" + {det}") } else if det < 0 { format!(" - {}", -det) } else { String::new() };
    let poly = format!("\\lambda^2{}{constant}", if trace != 0 { term(-trace, "\\lambda", false) } else { String::new() });
    let diag = "Eigenvalues are the diagonal entries only for a triangular matrix; solve $\\det(A - \\lambda I) = 0$.";
    Spec::new(format!("The matrix $A = {}$ has two integer eigenvalues. What is the {which} one?", mat(&a)), answer, vec![
        step("Trace", format!("{} + {}", p(pp), p(t)), trace),
        step("Determinant", d2_tex(&a), det),
        line("Characteristic polynomial $\\det(A - \\lambda I) = \\lambda^2 - (\\operatorname{tr} A)\\lambda + \\det A$", format!("{poly} = 0")),
        step("Discriminant", format!("{}^2 - 4 \\cdot {}", p(trace), p(det)), disc),
        step(format!("The {which} root"), format!("\\frac{{{trace} {sign} \\sqrt{{{disc}}}}}{{2}}"), answer),
    ]).mistakes(vec![
        wrong(other, format!("That's the {} eigenvalue.", if larger { "smaller" } else { "larger" })),
        wrong(pp, diag),
        wrong(t, diag),
        wrong(-other, "The characteristic polynomial is $\\lambda^2 - (\\operatorname{tr} A)\\lambda + \\det A$: check the trace term's sign."),
        wrong(if larger { trace + root } else { trace - root }, "Divide by 2: $\\lambda = \\frac{\\operatorname{tr} A \\pm \\sqrt{\\Delta}}{2}$."),
    ])
}
