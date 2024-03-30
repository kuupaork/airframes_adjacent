You should already have done this at a shell prompt (or else you're reading this on the web):

```
git clone https://github.com/kuupaork/airframes_adjacent
```

then 

```
cd airframes_adjacent/dumbhfdl
```

You also need to make sure you have the dependencies required. Obviously, dumphfdl is required, and I assume you have that installed.

```
sudo apt install python3-click python3-requests
```

Now, copy one of the starter environment files (currently only "airspyhf.env" exists) to `.env` in the same directory.

```
cp airspy.hf .env
```

Edit the `.env` file in your favourite text editor, following the minimal instructions in it, or the more complete ones in the `README.md` file

Once you're comfortable with your configuration, you can run

```
./dumbhfdl.sh
```

and it should start up fine.
