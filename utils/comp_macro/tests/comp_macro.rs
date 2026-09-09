use comp_python_macro::comp;

#[test]
fn it_works() {
    let result = comp![x for x in [1, 2, 3]].collect::<Vec<_>>();
    assert_eq!(result, [1, 2, 3])
}

#[test]
fn mapping_works() {
    let result = comp![x + 1 for x in [1, 2, 3]].collect::<Vec<_>>();
    assert_eq!(result, [2, 3, 4])
}

#[test]
fn filtering_less_zero_works() {
    let result = comp![x + 1 for x in [-1, -2, 0, 1, 2, 3] if x > 0].collect::<Vec<_>>();
    assert_eq!(result, [2, 3, 4])
}

#[test]
fn filtering_not_three_works() {
    let result = comp![x + 1 for x in [0, 1, 2, 3] if x != 3].collect::<Vec<_>>();
    assert_eq!(result, [1, 2, 3])
}

#[test]
fn filtering_by_index_works() {
    let vec = vec![0, 1, 2, 3].into_iter().enumerate();
    let result = comp![x for (i, x) in vec if i % 2 == 0].collect::<Vec<_>>();
    assert_eq!(result, [0, 2])
}

#[test]
fn nested_arrays() {
    let vec_of_vecs = vec![vec![1, 2, 3], vec![4, 5, 6]];
    let result = comp![x for vec in vec_of_vecs for x in vec if x > 0].collect::<Vec<_>>();
    assert_eq!(result, [1, 2, 3, 4, 5, 6])
}
