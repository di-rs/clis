# Rust comp proc_macro

Learning rust proc macro system with creating comp macro

Supporting comp Python syntax using Rust macro system 

```rs
comp![x for x in [1, 2, 3]]; // Basic loop

comp![x + 1 for x in [0, 1, 2, 3] if x != 3]; // With condition

comp![x + 1 for x in [0, 1, 2, 3] if x != 3 if x > 0] // With multiple conditions

comp![x for vec in vec_of_vecs for x in vec if x > 0] // Nested arrays
```

This was created using the awesome [Logan Smith's tutorial](https://www.youtube.com/watch?v=SMCRQj9Hbx8)
